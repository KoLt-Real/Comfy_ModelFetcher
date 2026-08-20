"""``output/<category>`` is a place a model CAN be, never a place to SEND one.

ComfyUI registers those folders in ``folder_names_and_paths`` so that models written by save
nodes stay loadable. They must therefore keep being scanned (duplicates, relink) but disappear
from the destination menu — and with it from what the API accepts.
"""
import os, sys, types, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmp = tempfile.mkdtemp()
models = os.path.join(tmp, "models")
out = os.path.join(tmp, "output")
ckpt = os.path.join(models, "checkpoints")
extra = os.path.join(tmp, "extra", "checkpoints")
out_ckpt = os.path.join(out, "checkpoints")
for d in (ckpt, extra, out_ckpt):
    os.makedirs(d, exist_ok=True)
os.makedirs(os.path.join(out_ckpt, "Runs"), exist_ok=True)
open(os.path.join(out_ckpt, "saved.safetensors"), "wb").write(b"\0" * 100)
open(os.path.join(out_ckpt, "Runs", "tucked.safetensors"), "wb").write(b"\0" * 100)

fp = types.ModuleType("folder_paths")
fp.models_dir = models
fp.map_legacy = lambda n: n
fp.get_output_directory = lambda: out
# ComfyUI's real order: the models/ folders first, output/ appended afterwards.
fp.folder_names_and_paths = {"checkpoints": ([ckpt, extra, out_ckpt], set())}
sys.modules["folder_paths"] = fp

from fetcher import scanner
from fetcher.scanner import CategoryInfo, build_disk_index, classify
from fetcher.notes_parser import ModelRef

fails = []
def ck(n, c, e=""):
    print(("OK   " if c else "FAIL ") + n + ("" if c else "  -> " + str(e)))
    if not c: fails.append(n)


ci = scanner.resolve_category("checkpoints")
dirs = [l["dir"] for l in scanner.list_locations(ci)]

ck("the output folder is not offered as a destination", out_ckpt not in dirs, dirs)
ck("the legitimate locations are still offered", dirs == [ckpt, extra], dirs)
ck("dest_dirs and the menu say the same thing", scanner.dest_dirs(ci) == dirs)
ck("output stays scanned (all_dirs untouched)", out_ckpt in ci.all_dirs, ci.all_dirs)
ck("the default destination is not inside output", not ci.target_dir.startswith(out))

# Excluded from the menu does not mean ignored: a model written into output/ is still seen.
index = build_disk_index(ci.all_dirs)
def status_of(fname):
    return classify(ModelRef(filename=fname, url="https://x/" + fname, category="checkpoints"),
                    ci, index, 100)

root = status_of("saved.safetensors")
ck("at the root of output: the loader finds it by name -> installed",
   root["status"] == scanner.ST_INSTALLED, root["status"])
ck("and it really is seen inside output", root["local_matches"]
   and root["local_matches"][0]["path"].startswith(out_ckpt), root["local_matches"])

tucked = status_of("tucked.safetensors")
ck("tucked in a subfolder of output: a relinkable duplicate",
   tucked["status"] == scanner.ST_DUP_SAME
   and tucked["relink"]["value"] == "Runs/tucked.safetensors", tucked["relink"])

# The default target must never land in output, even when it is the only existing dir.
missing = os.path.join(models, "loras_absent")
fp.folder_names_and_paths["loras"] = ([missing, os.path.join(out, "loras")], set())
os.makedirs(os.path.join(out, "loras"), exist_ok=True)
ck("default target: a missing models/ folder rather than an existing output folder",
   scanner.resolve_category("loras").target_dir == missing,
   scanner.resolve_category("loras").target_dir)

# Safety net: if EVERYTHING is under output, an imperfect menu beats zero destinations.
only_out = CategoryInfo(name="x", known=True, target_dir=out_ckpt, all_dirs=[out_ckpt])
ck("never zero destinations", scanner.dest_dirs(only_out) == [out_ckpt])

# Older ComfyUI / a stub without get_output_directory: no filtering, no exception.
del fp.get_output_directory
ck("without get_output_directory, nothing is filtered",
   scanner.dest_dirs(ci) == [ckpt, extra, out_ckpt], scanner.dest_dirs(ci))

print(f"\n{'FAILURES: ' + ', '.join(fails) if fails else 'output destinations OK'}")
sys.exit(1 if fails else 0)
