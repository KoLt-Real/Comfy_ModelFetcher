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
        # The popup lets the user pick WHICH copy Relink points at, out of `local_matches`.
        # That menu is only honest if every copy it lists carries a value that really reaches
        # it, AND if the automatic choice is one of them — a default absent from the menu
        # would show a selection that is not what the button does.
        cands = r["local_matches"]
        offered = [c for c in cands if remote is None or c["same_size"]]
        if not why:
            broken = next((c for c in cands if not c.get("value") or not c.get("root")
                           or os.path.realpath(os.path.join(c["root"], c["value"]))
                           != os.path.realpath(c["path"])), None)
            if broken:
                why = f"copy with no usable value: {broken['path']}"
            elif rl and not any(c["value"] == rl["value"] and c["root"] == rl["root"]
                                for c in offered):
                why = "the automatic choice is absent from the offered copies"
        if why:
            bad.append((combo, remote, st, rl and rl["value"], why))

print(f"\n--- invariant over {3 ** len(PLACES) * 2} disk layouts ---")
for b in bad[:5]:
    print("FAIL", b)
print(("OK  " if not bad else "FAIL") + f"  no dead-end row ({len(bad)} anomaly/ies)")
res.append(not bad)
# --- the case behind the feature: several copies, several subfolders ---------
# Two copies of the same file in two subfolders of the SAME root: the automatic pick answers
# for one of them, the menu must show both so the user can overrule it.
multi = os.path.join(tmp, "multi")
mk(os.path.join(multi, "Flux", "dual.safetensors"), 100)
mk(os.path.join(multi, "archive", "dual.safetensors"), 100)
mk(os.path.join(multi, "old", "dual.safetensors"), 42)
cat_multi = CategoryInfo(name="checkpoints", known=True, target_dir=multi, all_dirs=[multi])
r = classify(ModelRef(filename="dual.safetensors", url="u", category="checkpoints"),
             cat_multi, build_disk_index([multi]), 100)
values = sorted(c["value"] for c in r["local_matches"])
offered = sorted(c["value"] for c in r["local_matches"] if c["same_size"])
print("\n--- several copies in several subfolders ---")
for label, got, want in (
    ("every copy is listed", values,
     ["Flux/dual.safetensors", "archive/dual.safetensors", "old/dual.safetensors"]),
    # A copy whose size contradicts the source stays out of the menu, for the same reason a
    # size mismatch never gets a Relink button: it is another version of the model.
    ("only the same-size copies are offered", offered,
     ["Flux/dual.safetensors", "archive/dual.safetensors"]),
    ("the automatic choice is one of them", r["relink"]["value"] in offered, True),
):
    ok = got == want
    print(("OK  " if ok else "FAIL") + f"  {label:38} {got!r}"
          + ("" if ok else f"   EXPECTED {want!r}"))
    res.append(ok)

# --- the automatic pick must not depend on the filesystem --------------------
# os.scandir order is neither sorted nor stable across filesystems. Left alone it decided both
# which copy the popup pre-selects and which of two copies sharing a widget value survives:
# two machines with identical trees would relink different files on the same click.
# "Zeta" and "alpha" on purpose: byte order would put Zeta first, alphabetical order — what
# the README documents and what a reader of the menu expects — puts alpha first.
order = os.path.join(tmp, "order")
for sub in ("zzz", "aaa", "Zeta", "alpha", "mmm"):
    mk(os.path.join(order, sub, "ord.safetensors"), 100)
cat_ord = CategoryInfo(name="checkpoints", known=True, target_dir=order, all_dirs=[order])
got = [c["value"] for c in classify(
    ModelRef(filename="ord.safetensors", url="u", category="checkpoints"),
    cat_ord, build_disk_index([order]), 100)["local_matches"]]
walked = [e.name for e in os.scandir(order)]
ok = got == sorted(got, key=str.casefold)
print("\n--- order independent of the filesystem ---")
print(("OK  " if ok else "FAIL") + f"  copies sorted {got!r}  (disk order was {walked!r})")
res.append(ok)

print(f"\n{sum(res)}/{len(res)} cases OK")
sys.exit(0 if all(res) else 1)