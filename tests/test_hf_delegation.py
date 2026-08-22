"""Delegation to huggingface_hub: HF sizes and token validation.

The library is faked (no network access) then removed, to check BOTH paths: delegation when it
is there, fallback when it is missing.
"""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fp = types.ModuleType("folder_paths")
fp.models_dir="/m"
fp.folder_names_and_paths={}
fp.get_user_directory=lambda:"/u"
fp.map_legacy=lambda n:n
sys.modules["folder_paths"] = fp

from fetcher import hf_token, remote

fails = []
def ck(n, c, e=""):
    print(("OK   " if c else "FAIL ") + n + ("" if c else "  -> " + str(e)))
    if not c: fails.append(n)

HF = "https://huggingface.co/org/repo/resolve/main/m.safetensors"
CIVITAI = "https://civitai.com/api/download/models/123"


def fake_hub(**attrs):
    """Inject a fake huggingface_hub; returns the call log."""
    log = {"meta_calls": [], "whoami_calls": []}
    mod = types.ModuleType("huggingface_hub")

    def get_hf_file_metadata(url, token=None, **kw):
        log["meta_calls"].append({"url": url, "token": token})
        if "raise_meta" in attrs:
            raise attrs["raise_meta"]
        return types.SimpleNamespace(size=attrs.get("size"))

    class HfApi:
        def __init__(self, token=None, **kw):
            self.token = token
        def whoami(self):
            log["whoami_calls"].append(self.token)
            if "raise_whoami" in attrs:
                raise attrs["raise_whoami"]
            return attrs.get("whoami", {"name": "kolt"})

    mod.get_hf_file_metadata = get_hf_file_metadata
    mod.HfApi = HfApi
    sys.modules["huggingface_hub"] = mod
    return log


def no_hub():
    """Simulate the library being absent."""
    sys.modules["huggingface_hub"] = None   # importing from None raises ImportError


def http_error(status):
    e = Exception("boom")
    e.response = types.SimpleNamespace(status_code=status)
    return e


# ---------- size: the delegated path ----------------------------------------
log = fake_hub(size=4096)
remote.invalidate()
ck("HF URL -> size through huggingface_hub", remote.remote_size(HF) == (4096, None))
ck("the library really was called", len(log["meta_calls"]) == 1, log)

os.environ["HF_TOKEN"] = "not-a-real-token"
remote.invalidate()
log["meta_calls"].clear()
remote.remote_size(HF)
ck("the token is passed to the library for an HF URL",
   log["meta_calls"][0]["token"] == "not-a-real-token", log["meta_calls"])

# ---------- size: translated errors -----------------------------------------
for status, expected in ((401, "401_gated"), (403, "401_gated"), (404, "404")):
    fake_hub(raise_meta=http_error(status))
    remote.invalidate()
    ck(f"HTTP {status} -> {expected}", remote.remote_size(HF) == (None, expected))

fake_hub(raise_meta=Exception("network down"))   # with no HTTP response
remote.invalidate()
ck("error with no HTTP status -> network", remote.remote_size(HF) == (None, "network"))

fake_hub(size=None)
remote.invalidate()
ck("metadata without a size -> no_size", remote.remote_size(HF) == (None, "no_size"))

# ---------- a non-HF URL NEVER goes through the hub -------------------------
log = fake_hub(size=999)
remote.invalidate()
calls = {"n": 0}
def fake_generic(url):
    calls["n"] += 1
    return (7, None)
remote._fetch_generic = fake_generic
ck("Civitai URL -> the generic path", remote.remote_size(CIVITAI) == (7, None))
ck("the hub is not called for a non-HF URL", log["meta_calls"] == [], log)
ck("no token is sent to a non-HF host", hf_token.auth_headers(CIVITAI) == {})

# ---------- fallback when the library is missing ----------------------------
no_hub()
remote.invalidate()
calls["n"] = 0
ck("without huggingface_hub, an HF URL falls back to the hand-rolled HEAD",
   remote.remote_size(HF) == (7, None) and calls["n"] == 1, calls)

# ---------- token validation ------------------------------------------------
log = fake_hub(whoami={"name": "kolt", "fullname": "K"})
ck("token validated through HfApi", hf_token.validate("not-a-real-token") == (True, "kolt"))
ck("the token tested is the one supplied, not the environment's",
   log["whoami_calls"] == ["not-a-real-token"], log)

fake_hub(raise_whoami=http_error(401))
ck("token refused -> invalid", hf_token.validate("not-a-valid-token") == (False, None))
ck("empty token -> invalid with no network call", hf_token.validate("  ") == (False, None))

fake_hub(whoami={"fullname": "No Name"})
ck("full name used when name is absent", hf_token.validate("not-a-real-token") == (True, "No Name"))

# HTTP fallback: HF_ENDPOINT must be honoured (mirror instances)
no_hub()
seen = {}
class FakeResp:
    status_code = 200
    def json(self): return {"name": "mirror-user"}
def fake_get(url, **kw):
    seen["url"] = url
    seen["headers"] = kw.get("headers")
    return FakeResp()
hf_token.requests = types.SimpleNamespace(get=fake_get, RequestException=Exception)
os.environ["HF_ENDPOINT"] = "https://hf.my-company.local"
ck("fallback: token validated", hf_token.validate("not-a-real-token") == (True, "mirror-user"))
ck("fallback: HF_ENDPOINT honoured",
   seen["url"] == "https://hf.my-company.local/api/whoami-v2", seen.get("url"))
del os.environ["HF_ENDPOINT"]

del os.environ["HF_TOKEN"]
print(f"\n{'FAILURES: ' + ', '.join(fails) if fails else 'huggingface_hub delegation OK'}")
sys.exit(1 if fails else 0)
