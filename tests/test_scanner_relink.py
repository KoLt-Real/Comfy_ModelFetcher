"""Checks relink_target/classify outside ComfyUI (no folder_paths import here)."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fetcher import scanner
from fetcher.scanner import CategoryInfo, classify, build_disk_index
from fetcher.notes_parser import ModelRef

def mk(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * size)

tmp = tempfile.mkdtemp()
ckpt = os.path.join(tmp, "models", "checkpoints")
extra = os.path.join(tmp, "extra", "checkpoints")
mk(os.path.join(ckpt, "Flux", "flux1-dev.safetensors"), 100)
mk(os.path.join(ckpt, "sd15.safetensors"), 100)
mk(os.path.join(extra, "wan.safetensors"), 100)
mk(os.path.join(extra, "SDXL", "sub", "deep.safetensors"), 100)
mk(os.path.join(ckpt, "Flux", "other.safetensors"), 42)

cat = CategoryInfo(name="checkpoints", known=True, target_dir=ckpt, all_dirs=[ckpt, extra])
index = build_disk_index([ckpt, extra])

def check(fname, remote_size, want_status, want_relink):
    ref = ModelRef(filename=fname, url="https://x/" + fname, category="checkpoints")
    r = classify(ref, cat, index, remote_size)
    got = r["relink"]["value"] if r["relink"] else None
    ok = (r["status"] == want_status and got == want_relink)
    print(("OK  " if ok else "FAIL") + f"  {fname:26} size={remote_size} -> {r['status']:20} relink={got!r}"
          + ("" if ok else f"   EXPECTED {want_status} / {want_relink!r}"))
    return ok

res = [
    # tucked in a subfolder, same size = likely duplicate -> relink
    check("flux1-dev.safetensors", 100, scanner.ST_DUP_SAME, "Flux/flux1-dev.safetensors"),
    # at the root of the category -> installed, no relink
    check("sd15.safetensors", 100, scanner.ST_INSTALLED, None),
    # root of an extra path: the loader finds it by its bare name -> INSTALLED, nothing to do.
    # (reporting it as "likely duplicate" with no relink left a dead-end row: the bug seen
    #  after a download aimed at an extra path)
    check("wan.safetensors", 100, scanner.ST_INSTALLED, None),
    # deep subfolder of an extra path
    check("deep.safetensors", 100, scanner.ST_DUP_SAME, "SDXL/sub/deep.safetensors"),
    # same name but different size -> no relink (the file is probably another version)
    check("other.safetensors", 100, scanner.ST_DUP_DIFF, None),
    # unknown remote size: assumed duplicate, relink offered
    check("flux1-dev.safetensors", None, scanner.ST_DUP_SAME, "Flux/flux1-dev.safetensors"),
    # not on disk
    check("nope.safetensors", 100, scanner.ST_MISSING, None),
]
# --- invariant: never a dead-end row ----------------------------------------
# "Likely duplicate" means "you have the file, but the node cannot find it": such a row MUST
# therefore always carry a Relink. The case that was missing: a file sitting at the root of an
# extra path (a 2nd ComfyUI install) — the loader finds it by its bare name, so it is
# INSTALLED, not a duplicate. It used to read "likely duplicate" with nothing to do.
import itertools

inv = os.path.join(tmp, "inv")
A, B = os.path.join(inv, "A"), os.path.join(inv, "B")   # A = main folder, B = extra path
PLACES = [(A, ""), (A, "Sub"), (B, ""), (B, "Deep/er")]
cat_inv = CategoryInfo(name="checkpoints", known=True, target_dir=A, all_dirs=[A, B])
RIGHT, WRONG = 100, 55

bad = []
for n, combo in enumerate(itertools.product([None, RIGHT, WRONG], repeat=len(PLACES))):
    fname = f"inv{n:03d}.safetensors"
    for (root, sub), size in zip(PLACES, combo):
        if size is None:
            continue
        mk(os.path.join(root, *sub.split("/"), fname) if sub else os.path.join(root, fname), size)
    for remote in (RIGHT, None):
        r = classify(ModelRef(filename=fname, url="u", category="checkpoints"),
                     cat_inv, build_disk_index([A, B]), remote)
        st, rl = r["status"], r["relink"]
        # a "usable" copy at the root = right size as soon as the remote one is known
        usable_root = any(size is not None and (remote is None or size == remote)
                          for (_, sub), size in zip(PLACES, combo) if sub == "")
        why = None
        if st == scanner.ST_DUP_SAME and not rl:
            why = "likely duplicate WITHOUT relink (dead-end row)"
        elif rl and st != scanner.ST_DUP_SAME:
            why = f"relink offered on status {st}"
        elif rl and ("/" not in rl["value"] or not os.path.isfile(rl["path"])):
            why = f"invalid relink target {rl['value']!r}"
        elif st == scanner.ST_INSTALLED and not usable_root:
            why = "installed while no usable copy sits at a root"
        elif st == scanner.ST_MISSING and any(s is not None for s in combo):
            why = "missing while the file is on disk"
        elif usable_root and st != scanner.ST_INSTALLED:
            why = f"usable copy at a root but status {st}"
        if why:
            bad.append((combo, remote, st, rl and rl["value"], why))

print(f"\n--- invariant over {3 ** len(PLACES) * 2} disk layouts ---")
for b in bad[:5]:
    print("FAIL", b)
print(("OK  " if not bad else "FAIL") + f"  no dead-end row ({len(bad)} anomaly/ies)")
res.append(not bad)
print(f"\n{sum(res)}/{len(res)} cases OK")
sys.exit(0 if all(res) else 1)
