"""Manual parser check against a folder of workflow templates. Usage:
    python -m fetcher._validate_parser <template_folder>
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fetcher.notes_parser import parse_notes  # noqa: E402


def collect_notes(data):
    notes = []

    def walk(nodes):
        for n in nodes or []:
            if "Note" in (n.get("type") or ""):
                wv = n.get("widgets_values") or []
                notes.append({
                    "node_id": n.get("id"),
                    "title": n.get("title") or "",
                    "text": wv[0] if wv else "",
                })

    walk(data.get("nodes"))
    for sg in data.get("definitions", {}).get("subgraphs", []) or []:
        walk(sg.get("nodes"))
    return notes


def main(folder):
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith(".json"):
            continue
        data = json.load(open(os.path.join(folder, fn)))
        notes = collect_notes(data)
        refs = parse_notes(notes)
        print(f"\n===== {fn}  ({len(notes)} notes, {len(refs)} models) =====")
        for r in refs:
            print(f"  [{r.category or '??? UNKNOWN DEST'}] {r.filename}")
            print(f"       {r.url}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python -m fetcher._validate_parser <template_folder>")
    main(sys.argv[1])
