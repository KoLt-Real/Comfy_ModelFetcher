"""Location labels — for the "Save to" menu and for the copies "Link to" offers:
discriminating, short, and without UI overflow.

   python tests/e2e/test_location_labels.py
"""
import os, sys
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ANALYZE as _BASE, serve  # noqa: E402

BASE, srv, _sandbox = serve()
fails = []
def ck(n, c, e=""):
    print(("OK   " if c else "FAIL ") + n + ("" if c else "  -> " + str(e)))
    if not c: fails.append(n)

def labels(pg, dirs):
    return pg.evaluate("(d) => window.popup.locationLabels(d)", dirs)

with sync_playwright() as pw:
    b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 1100, "height": 700})
    pg.on("pageerror", lambda e: ck("JS error: " + str(e), False))
    pg.goto(BASE + "/index.html")
    pg.wait_for_function("window.ready === true")

    # ---- the case from the screenshot: two different "models/clip" -----------
    real_dirs = [r"D:\ComfyUI_MAIN\models\text_encoders",
                 r"D:\ComfyUI_MAIN\models\clip",
                 r"E:\Shared\AI\models\clip",
                 r"D:\ComfyUI_ALT\models\text_encoders",
                 r"D:\ComfyUI_MAIN\output\clip"]
    out = labels(pg, real_dirs)
    ck("the 5 locations have DISTINCT labels", len(set(out)) == 5, out)
    ck("the two models/clip are told apart",
       len([o for o in out if o.endswith("models/clip")]) == 2
       and out[1] != out[2], out)
    ck("the distinguishing segment is visible",
       "ComfyUI_MAIN" in out[1] and "ComfyUI_MAIN" not in out[2], out)
    print("        labels obtained:", out)

    # ---- no regression when there is no ambiguity ---------------------------
    out = labels(pg, ["/comfy/models/checkpoints", "/comfy/models/loras"])
    ck("simple case: 2 segments as before", out == ["models/checkpoints", "models/loras"], out)

    # ---- one path being the suffix of another --------------------------------
    out = labels(pg, ["/models/clip", "/opt/extra/models/clip"])
    ck("suffix: told apart all the same", len(set(out)) == 2, out)

    # ---- deep paths: middle elided, uniqueness preserved ---------------------
    out = labels(pg, ["/mnt/disk1/a/b/c/models/clip", "/mnt/disk2/a/b/c/models/clip"])
    ck("deep paths: still distinct", len(set(out)) == 2, out)
    ck("deep paths: labels elided, hence short",
       all(len(o) <= 34 for o in out), out)
    print("        deep:", out)

    # ---- tricky case: eliding must not recreate an ambiguity ----------------
    out = labels(pg, ["/x1/c1/c2/c3/models/clip", "/x2/c1/c2/c3/models/clip",
                      "/x1/d1/d2/d3/models/clip"])
    ck("eliding does not reintroduce an ambiguity", len(set(out)) == 3, out)

    ck("a single location works", len(labels(pg, ["/comfy/models/vae"])) == 1)

    # ---- the copies offered by "Link to" ------------------------------------
    # Same requirement, other menu: every entry repeats the row's filename, so what has to be
    # readable is WHERE each copy lives — which tree, which subfolder.
    NAME = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    def copies(pg, rows):
        return pg.evaluate("(c) => window.popup.copyLabels(c)",
                           [{"root": r, "value": v, "path": r + "/" + v,
                             "size": 1, "same_size": True} for r, v in rows])

    out = copies(pg, [("/o/models/unet", "Flux/x.safetensors"),
                      ("/o/models/unet", "Archive/x.safetensors")])
    ck("one root: the subfolder alone locates the copy", out == ["Flux", "Archive"], out)

    # The case from a real install: same file under two roots of the SAME category, the
    # subfolders differing only in case. Unreadable while the label was the full path.
    out = copies(pg, [("/opt/01_ComfyUI/models/unet", f"Minimax/{NAME}"),
                      ("/opt/01_ComfyUI/models/diffusion_models", f"MiniMax/{NAME}")])
    ck("two roots: each entry says which one",
       out == ["models/unet · Minimax", "models/diffusion_models · MiniMax"], out)
    ck("the filename is never repeated", all(NAME not in o for o in out), out)

    # Two disks whose roots end the SAME way: the labels have to dig deeper.
    out = copies(pg, [("/opt/01_ComfyUI/models/diffusion_models", f"Minimax/{NAME}"),
                      ("/mnt/M/ComfyUI/models/diffusion_models", f"MiniMax/{NAME}")])
    ck("two disks: still told apart", len(set(out)) == 2, out)
    ck("two disks: the differing segment is shown",
       "01_ComfyUI" in out[0] and "01_ComfyUI" not in out[1], out)
    print("        two disks:", out)

    # Two copies sharing a root, a third elsewhere: the shared root must not be labelled
    # "(1)" and "(2)" by the uniqueness fallback.
    out = copies(pg, [("/o/models/unet", "A/x.st"), ("/o/models/unet", "B/x.st"),
                      ("/mnt/N/models/unet", "C/x.st")])
    ck("a root shared by two copies keeps one label",
       out[0].rsplit(" · ", 1)[0] == out[1].rsplit(" · ", 1)[0], out)
    ck("three copies, three distinct labels", len(set(out)) == 3, out)
    ck("no (1)/(2) fallback leaking into a label",
       not any("(1)" in o or "(2)" in o for o in out), out)

    # Same folder, filenames differing only in case: the folder alone would print twice.
    out = copies(pg, [("/o/models/unet", "Flux/Model.safetensors"),
                      ("/o/models/unet", "Flux/model.safetensors")])
    ck("same folder, case-only difference: labels stay distinct", len(set(out)) == 2, out)

    # Two deep folders sharing BOTH ends: middle-elision maps them to the same string, and the
    # filename fallback cannot break the tie (same file). The cascade must fall back to the
    # full folders — over budget, but distinct.
    out = copies(pg, [("/o/models/ckpt", "SDXL/AnimeStyle/Portrait/x.st"),
                      ("/o/models/ckpt", "SDXL/PhotoRealV2/Portrait/x.st")])
    ck("elision collision: labels still distinct", len(set(out)) == 2, out)
    ck("elision collision: the differing middle is shown",
       any("AnimeStyle" in o for o in out) and any("PhotoRealV2" in o for o in out), out)

    # A deep tree whose branches differ at the END: a CSS truncation would eat exactly that.
    deep = "SDXL/Realistic/Photography/Portrait/"
    out = copies(pg, [("/o/models/ckpt", deep + "v1/x.st"), ("/o/models/ckpt", deep + "v2/x.st")])
    ck("deep folders: distinct once elided", len(set(out)) == 2, out)
    ck("deep folders: short enough that the browser never truncates",
       all(len(o) <= 29 for o in out), out)
    print("        deep folders:", out)

    # ---- UI: the select must not overflow ------------------------------------
    long_dirs = [{"dir": r"D:\ComfyUI_MAIN\models\text_encoders", "exists": True,
                  "is_default": True, "subfolders": ["", "Flux"]},
                 {"dir": "/mnt/a/really/very/very/long/path/to/models/text_encoders",
                  "exists": True, "is_default": False, "subfolders": [""]},
                 {"dir": r"E:\Other\Install\models\text_encoders", "exists": False,
                  "is_default": False, "subfolders": [""]}]
    payload = dict(_BASE)
    payload["categories"] = dict(_BASE["categories"])
    payload["categories"]["checkpoints"] = dict(_BASE["categories"]["checkpoints"],
                                                locations=long_dirs)
    pg.evaluate("(p) => { window.api.payload = p; }", payload)
    pg.evaluate("() => { window.app.graph = { _nodes: [], setDirtyCanvas(){} }; }")
    pg.evaluate("() => window.popup.openPopup([{node_id:1,title:'n',text:'x'}])")
    pg.wait_for_selector(".cf-mf-locsel")

    metrics = pg.evaluate("""() => {
      const sel = document.querySelector('.cf-mf-locsel');
      const row = sel.closest('.cf-mf-row');
      const card = document.querySelector('.cf-mf-card');
      const action = row.querySelector('.cf-mf-action');
      return {
        selW: sel.getBoundingClientRect().width,
        selRight: sel.getBoundingClientRect().right,
        actionLeft: action.getBoundingClientRect().left,
        rowRight: row.getBoundingClientRect().right,
        cardRight: card.getBoundingClientRect().right,
        bodyScroll: document.querySelector('.cf-mf-body').scrollWidth,
        bodyClient: document.querySelector('.cf-mf-body').clientWidth,
      };
    }""")
    ck("the select stays within its max width (200px)", metrics["selW"] <= 201, metrics)
    ck("the select does not overlap the action column",
       metrics["selRight"] <= metrics["actionLeft"] + 1, metrics)
    ck("the row does not overflow the card",
       metrics["rowRight"] <= metrics["cardRight"] + 1, metrics)
    ck("no horizontal scrolling introduced",
       metrics["bodyScroll"] <= metrics["bodyClient"] + 1, metrics)
    ck("the full path stays in the tooltip",
       pg.evaluate("() => document.querySelector('.cf-mf-locsel option').title").endswith("text_encoders"))
    b.close()

srv.shutdown()
print(f"\n{'FAILURES: ' + ', '.join(fails) if fails else 'labels OK'}")
sys.exit(1 if fails else 0)
