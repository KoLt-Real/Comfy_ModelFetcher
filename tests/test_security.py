import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# folder_paths stub for hf_token / scanner
fp = types.ModuleType("folder_paths")
fp.models_dir = "/comfy/models"
fp.folder_names_and_paths = {}
fp.get_user_directory = lambda: "/comfy/user"
fp.map_legacy = lambda n: n
sys.modules["folder_paths"] = fp

from fetcher import hf_token, scanner

fails = []
def ck(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else "  -> " + str(extra)))
    if not cond: fails.append(name)

# --- S2: the token only ever goes to HuggingFace -----------------------------
os.environ["HF_TOKEN"] = "not-a-real-token"
ck("token -> huggingface.co resolve", "Authorization" in hf_token.auth_headers(
    "https://huggingface.co/org/repo/resolve/main/m.safetensors"))
ck("token -> cdn-lfs.huggingface.co", "Authorization" in hf_token.auth_headers(
    "https://cdn-lfs.huggingface.co/repo/abc"))
ck("token -> hf.co", "Authorization" in hf_token.auth_headers("https://hf.co/x/y"))
ck("NO token -> civitai", hf_token.auth_headers(
    "https://civitai.com/api/download/models/123") == {})
ck("NO token -> github", hf_token.auth_headers(
    "https://github.com/o/r/releases/download/v1/m.bin") == {})
ck("NO token -> attacker host", hf_token.auth_headers("http://attacker.example/x") == {})
ck("NO token -> lookalike huggingface.co.evil.com",
   hf_token.auth_headers("http://huggingface.co.evil.com/x") == {})
ck("NO token -> misleading suffix evilhuggingface.co",
   hf_token.auth_headers("http://evilhuggingface.co/x") == {})
del os.environ["HF_TOKEN"]
ck("no token in the env -> no header even on HF",
   hf_token.auth_headers("https://huggingface.co/x") == {})

# --- S1: an unknown category stays confined under models/ --------------------
def target(cat): return scanner.resolve_category(cat).target_dir
mroot = os.path.realpath("/comfy/models")
def under(cat):
    t = os.path.realpath(target(cat))
    return t == mroot or t.startswith(mroot + os.sep)
ck("a normal category lands under models/", target("mycat") == os.path.normpath("/comfy/models/mycat"))
ck("a legitimate subfolder is kept", target("cat/sub") == os.path.normpath("/comfy/models/cat/sub"))
ck("../../custom_nodes traversal blocked", under("../../custom_nodes/evil"), target("../../custom_nodes/evil"))
ck("absolute /etc traversal blocked", under("/etc/cron.d"), target("/etc/cron.d"))
ck("Windows ..\\.. traversal blocked", under("..\\..\\Windows"), target("..\\..\\Windows"))
ck("mixed cat/../.. blocked", under("checkpoints/../../../root"), target("checkpoints/../../../root"))

print(f"\n{'FAILURES: ' + ', '.join(fails) if fails else 'all security cases OK'}")
sys.exit(1 if fails else 0)
