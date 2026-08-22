"""C4: the dying-worker / enqueue race, and cancelling after a re-enqueue."""
import os, sys, types, queue, threading, time
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

# ---------- 1. The worker timing out exactly during an enqueue ---------------
class RacyQueue(queue.Queue):
    """Simulates the worker timing out at the exact instant a job lands."""
    armed = True
    def get(self, timeout=None):
        if self.armed:
            self.armed = False
            raise queue.Empty
        return super().get(timeout=timeout)

mgr = dl.DownloadManager()
mgr._q = RacyQueue()
done = threading.Event()
mgr._download = lambda job: done.set()

mgr.enqueue("job1", "http://x/f.safetensors", "/tmp/f.safetensors")
ck("job handled despite the simultaneous worker timeout", done.wait(5))

# ---------- 2. The worker steps down cleanly when the queue is empty ---------
dl.WORKER_IDLE_TIMEOUT = 0.2
mgr2 = dl.DownloadManager()
seen = []
mgr2._download = lambda job: seen.append(job.id)
mgr2.enqueue("a", "http://x/a", "/tmp/a")
time.sleep(0.6)  # let the worker process the job then time out
ck("worker de-registered after being idle", mgr2._worker is None, mgr2._worker)
mgr2.enqueue("b", "http://x/b", "/tmp/b")
time.sleep(0.5)
ck("a new worker starts for the next job", seen == ["a", "b"], seen)

# ---------- 3. Cancelling after a re-enqueue during the active download ------
dl.WORKER_IDLE_TIMEOUT = 30
mgr3 = dl.DownloadManager()
started = threading.Event()
observed = {"cancelled": False}

def slow_download(job):
    started.set()
    for _ in range(200):           # ~4 s max
        if job.cancel.is_set():
            observed["cancelled"] = True
            dl._push("cf_mf.error", {"id": job.id, "code": "cancelled"})
            return
        time.sleep(0.02)

mgr3._download = slow_download
mgr3.enqueue("X", "http://x/big", "/tmp/big")
ck("active download started", started.wait(3))
active_job = mgr3._active_job
mgr3.enqueue("X", "http://x/big", "/tmp/big")   # re-enqueue of the same id while active
ck("the mapping now points at the re-enqueue", mgr3._jobs["X"] is not active_job)
ck("cancel accepted", mgr3.cancel("X") is True)
t0 = time.monotonic()
while not observed["cancelled"] and time.monotonic() - t0 < 3:
    time.sleep(0.02)
ck("the ACTIVE download really was cancelled", observed["cancelled"])
ck("exactly one cancelled event (no duplicate)",
   sum(1 for e, p in events if p.get("code") == "cancelled" and p.get("id") == "X") == 1,
   [p for e, p in events if p.get("id") == "X"])

# ---------- 4. Plain cancellation of a queued job ----------------------------
mgr4 = dl.DownloadManager()
hold = threading.Event()
mgr4._download = lambda job: hold.wait(2)
mgr4.enqueue("run", "http://x/1", "/tmp/1")
time.sleep(0.2)
mgr4.enqueue("wait", "http://x/2", "/tmp/2")
ck("a pending job can be cancelled", mgr4.cancel("wait") is True)
ck("immediate cancelled event for a queued job",
   any(p.get("id") == "wait" and p.get("code") == "cancelled" for e, p in events))
ck("unknown id -> False", mgr4.cancel("doesnotexist") is False)
hold.set()

print(f"\n{'FAILURES: ' + ', '.join(fails) if fails else 'C4 OK'}")
sys.exit(1 if fails else 0)
