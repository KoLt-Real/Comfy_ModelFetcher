"""The handlers must not block the event loop, and must keep their contract.

Needs aiohttp, which ships with ComfyUI. Run outside a ComfyUI environment, the test skips
itself (exit code 77) instead of turning red.
"""
import os, sys, types, asyncio, time, tempfile, importlib.util
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if importlib.util.find_spec("aiohttp") is None:
    print("skipped: aiohttp missing (it ships with ComfyUI)")
    sys.exit(77)

# --- ComfyUI stubs ----------------------------------------------------------
tmp = tempfile.mkdtemp()
ckpt = os.path.join(tmp, "models", "checkpoints")
os.makedirs(os.path.join(ckpt, "Flux"), exist_ok=True)
open(os.path.join(ckpt, "Flux", "flux1-dev.safetensors"), "wb").write(b"\0" * 100)

out_ckpt = os.path.join(tmp, "output", "checkpoints")
os.makedirs(os.path.join(out_ckpt, "Saved"), exist_ok=True)

fp = types.ModuleType("folder_paths")
fp.models_dir = os.path.join(tmp, "models")
# ComfyUI also registers output/<category> (so saved models can be loaded again).
fp.folder_names_and_paths = {"checkpoints": ([ckpt, out_ckpt], set())}
fp.get_user_directory = lambda: tmp
fp.get_output_directory = lambda: os.path.join(tmp, "output")
fp.map_legacy = lambda n: n
sys.modules["folder_paths"] = fp

class _Routes:
    def __init__(self): self.handlers = {}
    def post(self, path):
        def deco(fn): self.handlers[("POST", path)] = fn; return fn
        return deco
    def get(self, path):
        def deco(fn): self.handlers[("GET", path)] = fn; return fn
        return deco

srv = types.ModuleType("server")
class PromptServer:
    class _I:
        routes = _Routes()
        def send_sync(self, *a): pass
    instance = _I()
srv.PromptServer = PromptServer
sys.modules["server"] = srv

from fetcher import routes as R, remote

fails = []
def ck(n, c, e=""):
    print(("OK   " if c else "FAIL ") + n + ("" if c else "  -> " + str(e)))
    if not c: fails.append(n)

class FakeReq:
    def __init__(self, body): self._b = body; self.query = {}
    async def json(self): return self._b

def body_of(resp):
    import json
    return json.loads(resp.body.decode() if isinstance(resp.body, bytes) else resp.text)

NOTE = {"node_id": 1, "title": "t", "text":
        "**checkpoints**\n- [flux1-dev.safetensors](https://huggingface.co/x/resolve/main/flux1-dev.safetensors)\n"}

# --- a SLOW (2 s) simulated remote HEAD, to measure the blocking -------------
def slow_size(url):
    time.sleep(2.0)
    return (100, None)
remote._fetch = slow_size
remote.invalidate()

async def main():
    analyze = R.routes.handlers[("POST", "/cf_mf/analyze")]
    count = R.routes.handlers[("POST", "/cf_mf/count")]

    # The loop must stay responsive DURING the analysis: make it beat in parallel.
    ticks = {"n": 0}
    async def heartbeat():
        while True:
            await asyncio.sleep(0.05)
            ticks["n"] += 1

    hb = asyncio.create_task(heartbeat())
    t0 = time.monotonic()
    resp = await analyze(FakeReq({"notes": [NOTE]}))
    elapsed = time.monotonic() - t0
    hb.cancel()

    data = body_of(resp)
    ck("analysis succeeded", data["ok"] is True)
    ck("the model is seen as a likely duplicate",
       data["models"][0]["status"] == "duplicate_same_size", data["models"][0]["status"])
    ck("relink offered", data["models"][0]["relink"]["value"] == "Flux/flux1-dev.safetensors")
    ck("category metadata present",
       "checkpoints" in data["categories"] and data["categories"]["checkpoints"]["locations"])
    ck("subfolders = union of the locations",
       data["categories"]["checkpoints"]["subfolders"] == ["", "Flux"],
       data["categories"]["checkpoints"]["subfolders"])
    ck("the output folder does not pollute the destination menu",
       [l["dir"] for l in data["categories"]["checkpoints"]["locations"]] == [ckpt],
       data["categories"]["checkpoints"]["locations"])
    # ~2 s of HEAD: the loop should have beaten ~40 times if it were not blocked
    ck("the event loop stayed responsive during the analysis",
       ticks["n"] > 20, f"{ticks['n']} beats in {elapsed:.1f}s")

    # count: no network, but a walk -> must stay non-blocking as well
    ticks["n"] = 0
    hb = asyncio.create_task(heartbeat())
    c = body_of(await count(FakeReq({"notes": [NOTE]})))
    hb.cancel()
    ck("count: contract preserved", c["ok"] and c["total"] == 1 and c["missing"] == 0, c)

    # download: what the menu does not offer, the API must refuse.
    dl = R.routes.handlers[("POST", "/cf_mf/download")]
    enqueued = []
    R.manager.enqueue = lambda *a, **k: enqueued.append(a)
    job = {"url": "https://x/a.safetensors", "filename": "a.safetensors", "category": "checkpoints"}
    d = body_of(await dl(FakeReq({"jobs": [
        dict(job, id="ok", base_dir=ckpt),
        dict(job, id="out", base_dir=out_ckpt),
    ]})))
    ck("download to a legitimate location: accepted",
       [q["id"] for q in d["queued"]] == ["ok"] and len(enqueued) == 1, d)
    ck("download to the output folder: refused",
       [(r["id"], r["reason"]) for r in d["rejected"]] == [("out", "destination path refused")], d)

    # Malformed notes (the shape, not the JSON): 400, never an unhandled 500. Seen live with
    # a hand-written payload sending bare strings where the frontend sends {node_id, title,
    # text} dicts — parse_notes would raise AttributeError deep in the request.
    for label, notes in (("a bare string in the list", ["just text"]),
                         ("notes not even a list", "just text")):
        for name, handler in (("analyze", analyze), ("count", count)):
            r = await handler(FakeReq({"notes": notes}))
            ck(f"{name}: {label} -> 400",
               r.status == 400 and body_of(r)["error"] == "invalid notes",
               (r.status, body_of(r)))

    # analysis with no URL at all (the _no_sizes path)
    empty = body_of(await analyze(FakeReq({"notes": [{"node_id": 2, "title": "", "text": "pas de lien"}]})))
    ck("note with no link -> 0 models, no error", empty["ok"] and empty["models"] == [], empty)

asyncio.run(main())
print(f"\n{'FAILURES: ' + ', '.join(fails) if fails else 'async routes OK'}")
sys.exit(1 if fails else 0)
