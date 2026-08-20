# AGENTS.md — Comfy Model Fetcher

Working notes for AI coding agents. Read this before touching the code.

## What this is

A ComfyUI extension that **registers no nodes**. It adds a top-bar button, a popup, and HTTP
routes under `/cf_mf`. Everything is wrapped defensively: an error here must never stop ComfyUI
from starting (see `__init__.py`).

## Layout

```
__init__.py          route registration + empty V3 extension shim (no nodes)
fetcher/
  notes_parser.py    pure parsing — no ComfyUI import, no I/O. Keep it that way.
  scanner.py         folder_paths → categories, disk index, classification, relink target
  remote.py          remote size via HEAD (cached)
  downloader.py      queue + single worker thread, .part writes, websocket progress
  routes.py          the /cf_mf/* API
  hf_token.py        token storage/validation + the HuggingFace-only auth header
web/                 frontend, plain ES modules — no build step, no bundler, no npm
  main.js            button, badge, note collection from the graph
  popup.js           the panel and all its state
  relink.js          reads/rewrites loader widgets in the open graph
docs/                README screenshots
tests/               see below — run them
```

## Invariants — do not break these

1. **The token goes to HuggingFace only.** `hf_token.auth_headers(url)` is the *single* place
   that builds an `Authorization` header, and it returns `{}` for any non-HF host. Model URLs
   come from workflow notes, which are untrusted input — a shared workflow must never be able
   to point the downloader at an attacker's server *with your credentials attached*. Never
   reintroduce a global `_auth_headers()`.
2. **Download destinations stay under the models directory.** `scanner.resolve_category()`
   strips `..` and absolute segments; `routes._safe_join()` re-checks containment with
   `scanner.is_under()`. A client-supplied `category` is untrusted. Both layers must stay.
3. **`base_dir` must be a folder already registered for that category** (main + extra paths).
   Never accept an arbitrary path from the client. The allowed set is `scanner.dest_dirs()` —
   the *same* function that builds the UI menu, so what is not offered is not accepted.
4. **Blocking I/O never runs on the aiohttp event loop.** `os.walk` over model directories
   (often network shares) and HEAD requests both go through `asyncio.to_thread`. Blocking the
   loop freezes all of ComfyUI, including websocket progress for the very downloads this
   plugin started.
5. **"Installed" means "the loader resolves the plain filename"** — i.e. the file is at the
   root of *any* of the category's roots, not only `target_dir`. Downloading into an extra
   path is a legitimate choice, and ComfyUI searches every registered root. `_at_dir_root()`
   decides this, and `relink_target()` uses the same predicate on purpose: a row labelled
   *Likely duplicate* must always have something to relink, or it is a dead end (status says
   "you have it", the only button says "download it again").
6. **Relink state is read from the graph, never cached.** `linkState()` is called on every
   render. A cached "already linked" flag would lie after an undo or a workflow switch.
7. **Popup preferences are keyed per workflow.** Several workflows are open at once; state must
   not leak between tabs. See `currentWorkflowKey()` and its epoch fallback.
8. **Model ids must be unique.** `parse_notes()` disambiguates two different URLs that resolve
   to the same `category/filename`. The id is both the UI row key and the server job id.
9. **A `.part` file is only ever appended to for the same source URL.** `_part_path()` mixes a
   hash of the URL into the name; resume decisions rely on it. Never go back to a fixed
   `<dest>.part`, or two models sharing a filename will be spliced into one corrupt file.
10. **`huggingface_hub` is the HF protocol authority.** File sizes (`get_hf_file_metadata`) and
   token validation (`HfApi.whoami`) delegate to it; both fall back to a direct HTTP call only
   when the import fails. Do not reintroduce a hand-rolled HF HEAD dance — a wrong size flips a
   model between "duplicate" and "different content".
11. **Frontend has no build step.** Plain ES modules loaded by ComfyUI from `web/`. Do not add
   a bundler, TypeScript, or npm dependencies.

## Conventions

- Everything is in English: code, comments, docstrings, test names, UI strings, log messages
  and commit messages.
- ComfyUI frontend internals are probed defensively (`?.`, `try/catch`) — they churn between
  releases. When you touch `app.*` internals, assume the shape may be absent.
- `folder_paths` is imported lazily (inside functions) so the modules stay testable outside
  ComfyUI.
- Match the surrounding style; keep changes surgical.

## Tests

```bash
./tests/run_all.sh
```

Python and JS tests need nothing beyond what ComfyUI already provides; the browser tests need
`pip install playwright && playwright install chromium`. A test that self-skips exits 77.

| Suite | Covers |
|---|---|
| `tests/test_scanner_classify.py` | status ladder, proven equivalent to the pre-simplification version over the full truth table |
| `tests/test_scanner_relink.py` | relink target: subfolders, extra paths, size mismatch; the *no dead-end row* invariant over 162 disk layouts |
| `tests/test_security.py` | token host allowlist, path-traversal confinement |
| `tests/test_notes_parser.py` | unique, stable, order-independent model ids |
| `tests/test_remote_cache.py` | sizes cached forever, errors briefly, `invalidate()` |
| `tests/test_downloader.py` | worker expiry/enqueue race, cancel after re-enqueue |
| `tests/test_downloader_resume.py` | resume against a real local server: Range honoured, ignored, stale `.part`, cancel keeps bytes |
| `tests/test_hf_delegation.py` | huggingface_hub delegation and its fallback, both paths |
| `tests/test_routes.py` | handlers stay off the event loop; response contract |
| `tests/js/relink.test.js` | widget matching, subgraphs, promoted-widget double counting |
| `tests/e2e/test_popup.py` | Relink flow, per-workflow state, error/flash rows |
| `tests/e2e/test_token_panel.py` | token panel: save, remove, env-vs-file |
| `tests/e2e/test_location_labels.py` | location labels stay unique and short; select cannot overflow the row |
| `tests/test_scanner_output_dirs.py` | `output/<category>` stays scanned but never becomes a destination |

The JS and browser tests copy the **real** `web/` sources into a temporary fake-ComfyUI tree at
run time — there is no duplicated copy to keep in sync.

## Traps found the hard way

- A **subgraph container node carries a promoted copy** of the inner node's widget. Both must be
  rewritten, but the pair counts as **one** target — otherwise Relink reports "×2" for a single
  loader. See `proxy` in `findTargets()`.
- ComfyUI lists model files **relative to each category root**, so a file at the root of an
  *extra* path is reachable by its bare name: no relink is needed there, only for real
  subfolders.
- `app.refreshComboInNodes()` resets widget values that are not in the refreshed list — never
  call it to "fix" a stale combo; add the value to `widget.options.values` instead.
- A widget's own value list is the only reliable way to tell two same-named models of different
  categories apart.
- The download worker exits after an idle timeout: it must de-register itself **while holding
  the lock**, or a job enqueued at that instant is never picked up.
- A server may answer a `Range` request with plain `200` (whole file). Anything other than
  `206` must truncate and restart, never append — appending would double the file.
- `416` means the `.part` is larger than the resource: it belongs to another version of the
  file, so it is discarded and the download restarts once.
- `folder_names_and_paths` is **not** a list of download destinations. ComfyUI registers
  `output/<category>` there so that models saved by nodes stay loadable; those folders must
  keep being scanned (a model really can live there, and relink must point at it) but must
  never appear in the destination menu — `scanner.dest_dirs()` is the one place that draws
  the line, and `resolve_category()` never picks its default target outside it.
- Location labels must be built by `locationLabels()`, never by slicing a fixed number of
  path segments: two `extra_model_paths.yaml` roots very often share their tail
  (`models/clip`), and a fixed slice renders them identically. It grows the label to the
  smallest depth that is unique, then elides the middle — and falls back to the full label if
  eliding would make two entries look alike again.
