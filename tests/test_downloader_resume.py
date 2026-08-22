"""Download resume: Range honoured, Range ignored, .part kept or discarded.

Serves a real local HTTP server — no external network access.
"""
import os, sys, types, http.server, socketserver, threading, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fp = types.ModuleType("folder_paths")
fp.models_dir="/m"
fp.folder_names_and_paths={}
fp.get_user_directory=lambda:"/u"
fp.map_legacy=lambda n:n
sys.modules["folder_paths"] = fp

import fetcher.downloader as dl

events = []
dl._push = lambda ev, payload: events.append((ev, payload))

fails = []
def ck(n, c, e=""):
    print(("OK   " if c else "FAIL ") + n + ("" if c else "  -> " + str(e)))
    if not c: fails.append(n)

BODY = bytes(range(256)) * 400          # 102,400 bytes of verifiable content
HALF = len(BODY) // 2

class Handler(http.server.BaseHTTPRequestHandler):
    honour_range = True                 # flipped by the tests
    served_ranges = []

    def do_GET(self):
        rng = self.headers.get("Range")
        Handler.served_ranges.append(rng)
        start = 0
        if rng and Handler.honour_range:
            start = int(rng.split("=")[1].split("-")[0])
            if start >= len(BODY):
                self.send_response(416)
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(BODY)-1}/{len(BODY)}")
        else:
            self.send_response(200)
        chunk = BODY[start:]
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, *a): pass

socketserver.TCPServer.allow_reuse_address = True
srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}/model.safetensors"

tmp = tempfile.mkdtemp()
dest = os.path.join(tmp, "model.safetensors")
part = dl._part_path(dest, URL)

# ---------- the .part name isolates the sources -----------------------------
other = dl._part_path(dest, "http://elsewhere/model.safetensors")
ck("two URLs -> two distinct .part files", part != other, part)
ck("the .part sits next to the destination", part.startswith(dest) and part.endswith(".part"))
ck("stable name for the same URL", part == dl._part_path(dest, URL))

mgr = dl.DownloadManager()

def run(job_id, overwrite=False):
    events.clear()
    mgr._download(dl._Job(job_id, URL, dest, overwrite))
    return [e for e in events]

# ---------- nominal resume --------------------------------------------------
open(part, "wb").write(BODY[:HALF])        # a previous attempt stopped halfway
Handler.served_ranges.clear()
evs = run("resume")
ck("Range requested at the right position", Handler.served_ranges == [f"bytes={HALF}-"],
   Handler.served_ranges)
ck("download completed", any(e[0] == "cf_mf.done" for e in evs), evs)
ck("final file complete and intact", open(dest, "rb").read() == BODY)
ck(".part removed after success", not os.path.exists(part))
prog = [p for e, p in evs if e == "cf_mf.progress"]
ck("progress starts from the resumed position, not from 0",
   all(p["downloaded"] >= HALF for p in prog), prog[:2])
ck("the announced total is that of the whole file",
   all(p["total"] == len(BODY) for p in prog), prog[:2])

# ---------- a server that ignores Range -------------------------------------
os.remove(dest)
open(part, "wb").write(BODY[:HALF])
Handler.honour_range = False
evs = run("noresume")
ck("server ignoring Range: the file is correct all the same",
   open(dest, "rb").read() == BODY)
Handler.honour_range = True

# ---------- .part too large (416) -> start over -----------------------------
os.remove(dest)
open(part, "wb").write(BODY + b"surplus")
evs = run("stale")
ck("a stale .part does not corrupt the result", open(dest, "rb").read() == BODY)

# ---------- cancellation: the bytes are KEPT --------------------------------
os.remove(dest)
job = dl._Job("cancel", URL, dest, False)
job.cancel.set()                            # cancelled before the 1st chunk
events.clear()
mgr._download(job)
ck("cancellation reported", any(p.get("code") == "cancelled" for _e, p in events), events)
ck("the .part is kept for the resume", os.path.exists(part), os.listdir(tmp))
ck("no incomplete model left under the final name", not os.path.exists(dest))

# the resume after a cancellation really picks up where it stopped
before = os.path.getsize(part)
Handler.served_ranges.clear()
run("after-cancel")
ck("resume after cancellation", Handler.served_ranges == [f"bytes={before}-"] if before else True,
   Handler.served_ranges)
ck("final file correct after cancel then resume", open(dest, "rb").read() == BODY)

# ---------- destination already present -------------------------------------
evs = run("exists")
ck("file already there -> no re-download",
   any(p.get("note") == "already_exists" for _e, p in evs), evs)

srv.shutdown()
print(f"\n{'FAILURES: ' + ', '.join(fails) if fails else 'resume OK'}")
sys.exit(1 if fails else 0)
