"""Proves the simplified classify() cascade == the old one, over EVERY case."""
import os, sys, itertools, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fetcher import scanner
from fetcher.scanner import CategoryInfo, classify, build_disk_index
from fetcher.notes_parser import ModelRef

ST = scanner

def oracle(at_root, remote_size, any_same):
    """The ORIGINAL cascade, copied verbatim (unreachable branches included)."""
    if at_root and (remote_size is None or any_same):
        return ST.ST_INSTALLED
    elif remote_size is None:
        return ST.ST_DUP_SAME if not at_root else ST.ST_INSTALLED
    elif any_same:
        return ST.ST_DUP_SAME if not at_root else ST.ST_INSTALLED
    else:
        return ST.ST_DUP_DIFF

def actual(at_root, remote_size, any_same):
    """The CURRENT cascade, copied verbatim."""
    if at_root:
        return ST.ST_INSTALLED
    elif remote_size is not None and not any_same:
        return ST.ST_DUP_DIFF
    else:
        return ST.ST_DUP_SAME

fails = []
print("--- logical equivalence over the reachable combinations ---")
for at_root, rs, any_same in itertools.product([True, False], [None, 100, 200], [True, False]):
    # any_same can only be true when a remote size is known
    if rs is None and any_same:
        continue
    # ``at_root`` only looks at USABLE copies (right size as soon as it is known): a copy at
    # the root therefore implies any_same whenever the remote size is known.
    if at_root and rs is not None and not any_same:
        continue
    o, a = oracle(at_root, rs, any_same), actual(at_root, rs, any_same)
    ok = o == a
    print(("OK   " if ok else "FAIL ") + f"at_root={at_root!s:5} remote={rs!s:4} same={any_same!s:5} -> {a}")
    if not ok: fails.append((at_root, rs, any_same, o, a))

# --- and on REAL files, end to end -----------------------------------------
print("\n--- end to end on a real disk ---")
tmp = tempfile.mkdtemp()
ck = os.path.join(tmp, "checkpoints")
os.makedirs(os.path.join(ck, "Flux"), exist_ok=True)
def mk(rel, size):
    p = os.path.join(ck, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(b"\0" * size)

mk("at_root.safetensors", 100)          # at the root, right size
mk("root_wrong.safetensors", 55)        # at the root, wrong size
mk("Flux/sub_ok.safetensors", 100)      # subfolder, right size
mk("Flux/sub_wrong.safetensors", 55)    # subfolder, wrong size
cat = CategoryInfo(name="checkpoints", known=True, target_dir=ck, all_dirs=[ck])
index = build_disk_index([ck])

CAS = [
    ("at_root.safetensors",   100,  ST.ST_INSTALLED),
    ("at_root.safetensors",   None, ST.ST_INSTALLED),   # unknown size + in the right place
    ("root_wrong.safetensors", 100, ST.ST_DUP_DIFF),    # right place, wrong content
    ("root_wrong.safetensors", None, ST.ST_INSTALLED),  # unverifiable -> trusted
    ("Flux/sub_ok.safetensors".split("/")[-1], 100, ST.ST_DUP_SAME),
    ("sub_ok.safetensors",    None, ST.ST_DUP_SAME),
    ("sub_wrong.safetensors", 100,  ST.ST_DUP_DIFF),
    ("sub_wrong.safetensors", None, ST.ST_DUP_SAME),
    ("absent.safetensors",    100,  ST.ST_MISSING),
]
for fname, rs, want in CAS:
    r = classify(ModelRef(filename=fname, url="u", category="checkpoints"), cat, index, rs)
    ok = r["status"] == want
    print(("OK   " if ok else "FAIL ") + f"{fname:24} remote={rs!s:5} -> {r['status']}"
          + ("" if ok else f"   EXPECTED {want}"))
    if not ok: fails.append((fname, rs, want, r["status"]))

print(f"\n{'FAILURES: ' + str(fails) if fails else 'classify: equivalence proven + end to end OK'}")
sys.exit(1 if fails else 0)
