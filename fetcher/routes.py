"""The Model Fetcher HTTP API, under ``/cf_mf``.

- ``POST /cf_mf/analyze``: MD notes → parsed models + installed/duplicate status + remote
  sizes + per-category subfolders.
- ``POST /cf_mf/download``: queue jobs (destination resolved server-side, path-traversal
  proof). Progress is pushed on the websocket (``cf_mf.*`` events).
- ``POST /cf_mf/cancel`` / ``GET /cf_mf/status``.
- ``GET|POST /cf_mf/token`` (+ ``/clear``): state and registration of the HuggingFace token.
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from aiohttp import web
from server import PromptServer

from . import hf_token, remote, scanner
from .downloader import manager
from .notes_parser import parse_notes

logger = logging.getLogger("comfyfactory.modelfetcher")
routes = PromptServer.instance.routes


async def _no_sizes() -> dict:
    """Empty counterpart of ``_probe_sizes`` when the note holds no URL at all."""
    return {}

# At plugin load time: reload a previously saved token into the environment.
hf_token.load_saved_into_env()


def _probe_sizes(urls: list[str]) -> dict[str, tuple[int | None, str | None]]:
    """HEAD the remote sizes, bounded parallelism. Must run off the event loop."""
    with ThreadPoolExecutor(max_workers=8) as ex:
        return dict(zip(urls, ex.map(remote.remote_size, urls)))


def _scan_disk(cat_cache: dict, all_dirs: list[str]) -> tuple[dict, dict]:
    """Disk index + per-category metadata. Must run off the event loop."""
    index = scanner.build_disk_index(all_dirs)
    categories = {}
    for cat_key, ci in cat_cache.items():
        display = ci.name or (cat_key or "")
        locations = scanner.list_locations(ci)
        # A category's subfolders are the union of its locations' subfolders, already
        # collected by list_locations: no need to walk the whole tree again.
        subfolders = sorted({s for loc in locations for s in loc["subfolders"]},
                            key=lambda s: (s != "", s.lower()))
        categories[display or "?"] = {
            "target_dir": ci.target_dir,
            "all_dirs": ci.all_dirs,
            "known": ci.known,
            "subfolders": subfolders,
            "locations": locations,
        }
    return index, categories


@routes.post("/cf_mf/analyze")
async def analyze(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    notes = body.get("notes") or []
    refs = parse_notes(notes)

    # Category resolution (once per name).
    cat_cache: dict[str | None, scanner.CategoryInfo] = {}
    for r in refs:
        if r.category not in cat_cache:
            cat_cache[r.category] = scanner.resolve_category(r.category)

    all_dirs: list[str] = []
    for ci in cat_cache.values():
        all_dirs.extend(ci.all_dirs)
    urls = list({r.url for r in refs})

    # Disk walk (possibly over network shares) and remote HEADs: both block and both overlap,
    # so they go to threads. Leaving them on the event loop would freeze ALL of ComfyUI — the
    # progress websocket included — for the duration of the analysis.
    (index, categories), sizes = await asyncio.gather(
        asyncio.to_thread(_scan_disk, cat_cache, all_dirs),
        asyncio.to_thread(_probe_sizes, urls) if urls else _no_sizes(),
    )

    models = []
    for r in refs:
        ci = cat_cache[r.category]
        rsize, rerr = sizes.get(r.url, (None, None))
        cls = scanner.classify(r, ci, index, rsize)
        models.append({
            "id": r.id,
            "filename": r.filename,
            "url": r.url,
            "category": ci.name if ci.name else (r.category or None),
            "category_known": ci.known,
            "status": cls["status"],
            "remote_size": rsize,
            "remote_size_error": rerr,
            "local_matches": cls["local_matches"],
            "other_category_matches": cls["other_category_matches"],
            # Likely duplicate: relative path to push back into the loader node (or None).
            "relink": cls["relink"],
            "source_note": r.source_note,
        })

    # Every known category (feeds the select for unknown destinations).
    try:
        import folder_paths
        known_categories = sorted(
            k for k in folder_paths.folder_names_and_paths
            if k not in ("configs", "custom_nodes", "diffusers", "classifiers")
        )
    except Exception:
        known_categories = []

    return web.json_response({
        "ok": True,
        "models": models,
        "categories": categories,
        "known_categories": known_categories,
        # Same definition as /cf_mf/token: hf_token alone decides "a token is present".
        "hf_token": hf_token.get_token() is not None,
    })


@routes.post("/cf_mf/count")
async def count(request):
    """Light count (no network request) of how many models are still to download.

    Drives the live refresh of the button badge on every workflow change. "To download" = no
    file of that name anywhere in the category's folders (the same-size/different-size
    duplicate distinction is not needed here).
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    refs = parse_notes(body.get("notes") or [])
    cat_cache: dict[str | None, scanner.CategoryInfo] = {}
    for r in refs:
        if r.category not in cat_cache:
            cat_cache[r.category] = scanner.resolve_category(r.category)
    all_dirs: list[str] = []
    for ci in cat_cache.values():
        all_dirs.extend(ci.all_dirs)
    # Called on every workflow change: the walk goes to a thread (see analyze).
    index = await asyncio.to_thread(scanner.build_disk_index, all_dirs)

    missing = 0
    for r in refs:
        cls = scanner.classify(r, cat_cache[r.category], index, None)
        if cls["status"] in (scanner.ST_MISSING, scanner.ST_UNKNOWN):
            missing += 1

    return web.json_response({"ok": True, "total": len(refs), "missing": missing})


def _safe_join(base: str, *parts: str) -> str | None:
    """Join under ``base``, refusing any escape (``..``, absolute paths).

    Confinement leans on ``scanner.is_under`` — the same predicate as the classification — so
    that there is only ever one definition of "inside".
    """
    base_real = os.path.realpath(base)
    candidate = os.path.realpath(os.path.join(base, *parts))
    return candidate if scanner.is_under(candidate, base_real) else None


@routes.post("/cf_mf/download")
async def download(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    jobs = body.get("jobs") or []
    queued, rejected = [], []
    for j in jobs:
        jid = j.get("id")
        url = (j.get("url") or "").strip()
        filename = (j.get("filename") or "").strip()
        category = j.get("category")
        subfolder = (j.get("subfolder") or "").strip().strip("/")
        base_dir = (j.get("base_dir") or "").strip()
        overwrite = bool(j.get("overwrite"))

        if not jid or not url or not filename:
            rejected.append({"id": jid, "reason": "missing fields"})
            continue
        if not url.lower().startswith(("http://", "https://")):
            rejected.append({"id": jid, "reason": "URL is not http(s)"})
            continue
        if os.sep in filename or (os.altsep and os.altsep in filename) or filename in (".", ".."):
            rejected.append({"id": jid, "reason": "invalid filename"})
            continue

        ci = scanner.resolve_category(category)
        # Root location: target_dir by default, or one of the category's destination
        # locations (extra paths included, output excluded) if the client asks for one —
        # never an arbitrary path.
        base = ci.target_dir
        if base_dir:
            wanted = os.path.realpath(base_dir)
            allowed = next((d for d in scanner.dest_dirs(ci) if os.path.realpath(d) == wanted), None)
            if allowed is None:
                rejected.append({"id": jid, "reason": "destination path refused"})
                continue
            base = allowed
        # The final target must stay under that location (a subfolder is allowed, but bounded).
        dest = _safe_join(base, subfolder, filename) if subfolder else \
            _safe_join(base, filename)
        if dest is None:
            rejected.append({"id": jid, "reason": "destination path refused"})
            continue

        manager.enqueue(jid, url, dest, overwrite=overwrite)
        queued.append({"id": jid, "dest": dest})

    return web.json_response({"ok": True, "queued": queued, "rejected": rejected})


@routes.post("/cf_mf/cancel")
async def cancel(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if body.get("all"):
        manager.cancel_all()
        return web.json_response({"ok": True})
    jid = body.get("id")
    ok = manager.cancel(jid) if jid else False
    return web.json_response({"ok": ok})


@routes.get("/cf_mf/status")
async def status(request):
    return web.json_response({"ok": True, **manager.status()})


# --- HuggingFace token: never returned to the client, only its state -------------

@routes.get("/cf_mf/token")
async def get_token_state(request):
    return web.json_response({
        "ok": True,
        "present": hf_token.get_token() is not None,
        "from_file": hf_token.has_saved_file(),
    })


@routes.post("/cf_mf/token")
async def set_token(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid JSON"}, status=400)

    token = (body.get("token") or "").strip()
    if not token:
        return web.json_response({"ok": False, "error": "empty token"}, status=400)

    # HTTP round-trip to HuggingFace (up to 10 s): off the event loop.
    valid, username = await asyncio.to_thread(hf_token.validate, token)
    if not valid:
        return web.json_response({"ok": True, "valid": False})
    hf_token.save_token(token)
    # Gated models were cached as "401_gated": clear that so the re-analysis that follows
    # finally sees their size (otherwise ComfyUI would have to be restarted).
    remote.invalidate()
    return web.json_response({"ok": True, "valid": True, "username": username})


@routes.post("/cf_mf/token/clear")
async def clear_token(request):
    hf_token.clear_token()
    remote.invalidate()
    return web.json_response({"ok": True})
