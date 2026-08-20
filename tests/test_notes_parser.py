"""Note parsing: uniqueness of the model identifiers."""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fp = types.ModuleType("folder_paths"); fp.models_dir="/m"; fp.folder_names_and_paths={}
fp.get_user_directory=lambda:"/u"; fp.map_legacy=lambda n:n
sys.modules["folder_paths"] = fp

fails=[]
def ck(n,c,e=""):
    print(("OK   " if c else "FAIL ")+n+("" if c else "  -> "+str(e)));  fails.append(n) if not c else None

from fetcher.notes_parser import parse_notes
# ---------- C5: two URLs, same name + category -> distinct ids ---------------
note = {"node_id": 1, "title": "t", "text": """
**checkpoints**
- [model.safetensors](https://huggingface.co/repoA/resolve/main/model.safetensors)
- [model.safetensors](https://huggingface.co/repoB/resolve/main/model.safetensors)
- [autre.safetensors](https://huggingface.co/repoC/resolve/main/autre.safetensors)
"""}
refs = parse_notes([note])
ids = [r.id for r in refs]
ck("3 models parsed", len(refs) == 3, ids)
ck("all ids unique", len(set(ids)) == 3, ids)
ck("the unambiguous model keeps a readable id",
   "checkpoints/autre.safetensors" in ids, ids)
ck("same-named models get a suffix", all("#" in i for i in ids if "model.safetensors" in i), ids)

# stability: re-analysing the same notes gives the same ids back (prefs are keyed on them)
ck("ids stable across analyses", [r.id for r in parse_notes([note])] == ids)
# reversed order in the note -> same ids (the suffix derives from the URL, not the rank)
note2 = dict(note, text=note["text"].replace("repoA", "TMP").replace("repoB", "repoA").replace("TMP", "repoB"))
ck("ids independent of the order", set(r.id for r in parse_notes([note2])) == set(ids))


print(f"\n{'FAILURES: '+', '.join(fails) if fails else 'parser OK'}")
sys.exit(1 if fails else 0)
