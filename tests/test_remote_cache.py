"""Remote size cache: an error is never final."""
import os, sys, types, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fp = types.ModuleType("folder_paths"); fp.models_dir="/m"; fp.folder_names_and_paths={}
fp.get_user_directory=lambda:"/u"; fp.map_legacy=lambda n:n
sys.modules["folder_paths"] = fp

fails=[]
def ck(n,c,e=""):
    print(("OK   " if c else "FAIL ")+n+("" if c else "  -> "+str(e)));  fails.append(n) if not c else None

from fetcher import remote

# ---------- C1: an error must not be final -----------------------------------
calls = {"n": 0}
def fake_fetch(url):
    calls["n"] += 1
    return fake_fetch.result
remote._fetch = fake_fetch
remote.invalidate()

fake_fetch.result = (None, "401_gated")
ck("1st call -> gated", remote.remote_size("u1") == (None, "401_gated"))
ck("2nd call served from cache (no re-probe)", calls["n"] == 1)

# the token arrives: invalidate() must clear the cache
fake_fetch.result = (123, None)
remote.invalidate()
ck("after invalidate -> size finally seen", remote.remote_size("u1") == (123, None))
ck("re-probed exactly once", calls["n"] == 2)
ck("a success is cached for good", remote.remote_size("u1") == (123, None) and calls["n"] == 2)

# TTL: an error expires on its own
remote.invalidate()
remote.ERROR_TTL = 0.05
fake_fetch.result = (None, "network")
remote.remote_size("u2")
n_before = calls["n"]
time.sleep(0.08)
fake_fetch.result = (999, None)
ck("error expired by the TTL -> re-probed", remote.remote_size("u2") == (999, None))
ck("the re-probe really happened", calls["n"] == n_before + 1)
remote.invalidate()


print(f"\n{'FAILURES: '+', '.join(fails) if fails else 'remote cache OK'}")
sys.exit(1 if fails else 0)
