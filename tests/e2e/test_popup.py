"""Popup E2E: Relink, preference persistence, isolation between workflows.

   python tests/e2e/test_popup.py   (needs: pip install playwright && playwright install chromium)
"""
import copy, os, sys
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ANALYZE, serve  # noqa: E402

BASE, srv, _sandbox = serve()

fails = []
def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else "  -> " + str(extra)))
    if not cond:
        fails.append(name)

# Workflow A's graph: a loader points at the root, another one is already correct elsewhere.
GRAPH_A = """
() => {
  window.app.graph = {
    _nodes: [{
      type: "CheckpointLoaderSimple",
      widgets: [{ name: "ckpt_name", value: "flux1-dev.safetensors",
                  options: { values: ["other.safetensors", "Flux/flux1-dev.safetensors"] } }],
      setDirtyCanvas() {},
    }],
    setDirtyCanvas() {},
  };
}
"""
# Workflow B: no node uses flux1-dev.
GRAPH_B = """
() => {
  window.app.graph = {
    _nodes: [{ type: "KSampler", widgets: [{ name: "sampler_name", value: "euler",
               options: { values: ["euler"] } }], setDirtyCanvas() {} }],
    setDirtyCanvas() {},
  };
}
"""

def row(page, mid):
    return page.locator(f'.cf-mf-row[data-id="{mid}"]')

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page()
    page.on("pageerror", lambda e: check("JS error: " + str(e), False))
    page.goto(BASE + "/index.html")
    page.wait_for_function("window.ready === true")
    page.evaluate("(p) => { window.api.payload = p; }", ANALYZE)
    page.evaluate(GRAPH_A)

    FLUX = "checkpoints/flux1-dev.safetensors"
    ORPHAN = "vae/orphan.safetensors"
    DL = "checkpoints/tobedl.safetensors"

    # ---- 1. Initial render --------------------------------------------------
    page.evaluate("() => window.popup.openPopup([{node_id:1,title:'n',text:'x'}])")
    page.wait_for_selector(".cf-mf-row")
    check("4 rows rendered", page.locator(".cf-mf-row").count() == 4)
    check("Relink button present and enabled",
          row(page, FLUX).locator(".cf-mf-relink").is_enabled())
    check("initial badge = likely duplicate",
          "Likely duplicate" in row(page, FLUX).locator(".cf-mf-badge").inner_text())
    check("no node concerned -> button disabled",
          row(page, ORPHAN).locator(".cf-mf-relink").is_disabled())
    check("explicit tooltip on that button",
          "No node" in (row(page, ORPHAN).locator(".cf-mf-relink").get_attribute("title") or ""))
    check("save reminder hidden at first",
          page.locator(".cf-mf-save-hint").is_hidden())
    # Each action sits on its own line, next to the control that drives it.
    check("a single copy shows its path instead of a one-entry menu",
          row(page, ORPHAN).locator(".cf-mf-copyfixed").inner_text() == "sub/orphan.safetensors")
    check("Relink and its control share a line",
          row(page, FLUX).locator(".cf-mf-opline:not([hidden]) .cf-mf-relink").count() == 1)
    check("a separator closes the relink line, reading OR",
          row(page, FLUX).locator(".cf-mf-opsep").inner_text() == "OR")
    check("but no orphan separator when there is nothing to relink",
          row(page, DL).locator(".cf-mf-opsep").is_visible() is False)
    check("a row with nothing to relink has no relink line",
          row(page, DL).locator(".cf-mf-action-link").count() == 0
          or row(page, DL).locator(".cf-mf-opline[hidden]").count() == 1)

    # ---- 2. Relink click: state change + feedback ---------------------------
    row(page, FLUX).locator(".cf-mf-relink").click()
    check("the button gave way to a pill",
          row(page, FLUX).locator(".cf-mf-relink").count() == 0)
    badge = row(page, FLUX).locator(".cf-mf-relinked-badge")
    check("\"Linked\" pill displayed", badge.is_visible() and "Linked" in badge.inner_text(),
          badge.inner_text() if badge.count() else "missing")
    check("tooltip: path + save reminder",
          "Flux/flux1-dev.safetensors" in (badge.get_attribute("title") or "")
          and "Save the workflow" in (badge.get_attribute("title") or ""))
    check("row status switched to \"Linked\"",
          "Linked" in row(page, FLUX).locator(".cf-mf-badge").inner_text())
    check("save reminder displayed", page.locator(".cf-mf-save-hint").is_visible())
    check("node widget fixed",
          page.evaluate("() => window.app.graph._nodes[0].widgets[0].value")
          == "Flux/flux1-dev.safetensors")
    check("confirmation animation on the pill",
          page.evaluate("() => getComputedStyle(document.querySelector('.cf-mf-relinked-badge')).animationName")
          == "cf-mf-pop")
    check("confirmation animation on the row",
          page.evaluate("() => getComputedStyle(document.querySelector('.cf-mf-row-flash')).animationName")
          == "cf-mf-row-flash")
    check("green pill (a colour distinct from the amber button)",
          page.evaluate("() => getComputedStyle(document.querySelector('.cf-mf-relinked-badge')).color")
          == "rgb(110, 231, 160)")

    # ---- 3. Preferences entered, then close/reopen --------------------------
    row(page, DL).locator(".cf-mf-subinput").fill("SDXL/perso")
    row(page, DL).locator(".cf-mf-cb").uncheck()
    row(page, ORPHAN).locator(".cf-mf-cb").check()
    page.evaluate("() => window.popup.closePopup()")
    check("popup closed", page.evaluate("() => window.popup.isPopupOpen()") is False)
    page.evaluate("() => window.popup.openPopup()")
    page.wait_for_selector(".cf-mf-row")
    check("subfolder restored",
          row(page, DL).locator(".cf-mf-subinput").input_value() == "SDXL/perso")
    check("unticked box restored", not row(page, DL).locator(".cf-mf-cb").is_checked())
    check("ticked box restored", row(page, ORPHAN).locator(".cf-mf-cb").is_checked())
    check("Relink state read back from the graph (pill, not button)",
          row(page, FLUX).locator(".cf-mf-relinked-badge").count() == 1
          and row(page, FLUX).locator(".cf-mf-relink").count() == 0)
    check("save reminder still there", page.locator(".cf-mf-save-hint").is_visible())

    # ---- 4. Another workflow: no contamination ------------------------------
    page.evaluate("() => { window.app.extensionManager.workflow.activeWorkflow.key = 'workflow-B.json'; }")
    page.evaluate(GRAPH_B)
    page.evaluate("() => window.popup.openPopup()")
    page.wait_for_selector(".cf-mf-row")
    check("B: blank subfolder",
          row(page, DL).locator(".cf-mf-subinput").input_value() == "")
    check("B: boxes at their default values",
          row(page, DL).locator(".cf-mf-cb").is_checked()
          and not row(page, ORPHAN).locator(".cf-mf-cb").is_checked())
    check("B: relink not inherited from A",
          row(page, FLUX).locator(".cf-mf-relinked-badge").count() == 0)
    check("B: button disabled (no node mentions the file)",
          row(page, FLUX).locator(".cf-mf-relink").is_disabled())
    check("B: save reminder hidden", page.locator(".cf-mf-save-hint").is_hidden())

    # ---- 5. Back on A: the preferences are found again ----------------------
    page.evaluate("() => { window.app.extensionManager.workflow.activeWorkflow.key = 'workflow-A.json'; }")
    page.evaluate(GRAPH_A)
    page.evaluate("() => { window.app.graph._nodes[0].widgets[0].value = 'Flux/flux1-dev.safetensors'; }")
    page.evaluate("() => window.popup.openPopup()")
    page.wait_for_selector(".cf-mf-row")
    check("A: subfolder found again",
          row(page, DL).locator(".cf-mf-subinput").input_value() == "SDXL/perso")
    check("A: boxes found again",
          not row(page, DL).locator(".cf-mf-cb").is_checked()
          and row(page, ORPHAN).locator(".cf-mf-cb").is_checked())
    check("A: link still recognised", row(page, FLUX).locator(".cf-mf-relinked-badge").count() == 1)
    check("A: save reminder found again", page.locator(".cf-mf-save-hint").is_visible())

    # ---- 6. Ctrl+Z on the graph side: the UI tells the truth again ----------
    page.evaluate("() => { window.app.graph._nodes[0].widgets[0].value = 'flux1-dev.safetensors'; }")
    page.evaluate("() => window.popup.openPopup()")
    page.wait_for_selector(".cf-mf-row")
    check("link undone in the graph -> Relink button back",
          row(page, FLUX).locator(".cf-mf-relink").is_enabled()
          and row(page, FLUX).locator(".cf-mf-relinked-badge").count() == 0)


    # ---- 7. C6: a slow analysis must not paint over a more recent one -------
    page.evaluate("() => { window.app.extensionManager.workflow.activeWorkflow.key = 'workflow-A.json'; }")
    page.evaluate(GRAPH_A)
    page.evaluate("() => window.popup.openPopup()")
    page.wait_for_selector(".cf-mf-row")
    # SLOW analysis fired on A, then a switch to B which fires a fast one
    page.evaluate("() => { window.api.delayMs = 1500; window.popup.openPopup(); }")
    page.evaluate("() => { window.app.extensionManager.workflow.activeWorkflow.key = 'workflow-B.json'; }")
    page.evaluate(GRAPH_B)
    page.evaluate("() => window.popup.openPopup()")
    page.wait_for_selector(".cf-mf-row")
    page.wait_for_timeout(2000)   # let A's slow answer land
    check("B: A's slow answer did not repaint",
          row(page, FLUX).locator(".cf-mf-relink").is_disabled())
    check("B: boxes still at their defaults after the late answer",
          row(page, DL).locator(".cf-mf-cb").is_checked()
          and not row(page, ORPHAN).locator(".cf-mf-cb").is_checked())
    check("B: save reminder still hidden", page.locator(".cf-mf-save-hint").is_hidden())

    # ---- 8. C6: relink refused when the graph is no longer the analysed one -
    page.evaluate("() => { window.app.extensionManager.workflow.activeWorkflow.key = 'workflow-A.json'; }")
    page.evaluate(GRAPH_A)
    page.evaluate("() => window.popup.openPopup()")
    page.wait_for_selector(".cf-mf-row")
    # switch WITHOUT re-analysing (the popup stays on A's data)
    page.evaluate("() => { window.app.extensionManager.workflow.activeWorkflow.key = 'workflow-C.json'; }")
    row(page, FLUX).locator(".cf-mf-relink").click()
    # The message belongs to the relink line, not to the download one.
    check("relink refused on a workflow that changed",
          "Workflow changed" in row(page, FLUX).locator(".cf-mf-action-link").inner_text(),
          row(page, FLUX).locator(".cf-mf-action-link").inner_text())
    check("and Download was not the one blamed",
          "Workflow changed" not in row(page, FLUX).locator(".cf-mf-action").inner_text())
    check("the graph was NOT modified",
          page.evaluate("() => window.app.graph._nodes[0].widgets[0].value")
          == "flux1-dev.safetensors")


    # ---- 9. The 3 row defects, driven by the real events --------------------
    MYST = "?/mystery.safetensors"
    page.evaluate("() => { window.app.extensionManager.workflow.activeWorkflow.key = 'workflow-D.json'; }")
    page.evaluate(GRAPH_A)

    # (a) a late /status vs a "done" that landed meanwhile
    page.evaluate("(id) => { window.api.statusPayload = {ok:true, pending:[], active:id};"
                  " window.api.statusDelayMs = 800; }", DL)
    page.evaluate("() => window.popup.openPopup()")
    page.wait_for_selector(".cf-mf-row")
    page.evaluate("(id) => window.api.emit('cf_mf.done', {id})", DL)
    check("download finished -> row installed",
          "Installed" in row(page, DL).locator(".cf-mf-action").inner_text())
    page.wait_for_timeout(1300)   # the late /status answer lands now
    check("a late /status does not revive the finished row",
          "Installed" in row(page, DL).locator(".cf-mf-action").inner_text(),
          row(page, DL).locator(".cf-mf-action").inner_text())
    page.evaluate("() => { window.api.statusPayload = {ok:true, pending:[], active:null}; }")

    # (b) the box unticked at download time is remembered
    page.evaluate("() => window.popup.openPopup()")
    page.wait_for_selector(".cf-mf-row")
    check("downloaded model -> box not re-ticked on reopening",
          not row(page, DL).locator(".cf-mf-cb").is_checked())

    # (c) flash message on a row IN ERROR: Retry must come back
    page.locator(f'.cf-mf-row[data-id="{MYST}"] .cf-mf-catsel').select_option("checkpoints")
    row(page, MYST).locator("button.cf-mf-dl-one").click()          # Download
    page.evaluate("(id) => window.api.emit('cf_mf.error', {id, message:'network boom'})", MYST)
    check("row in error: Retry offered",
          row(page, MYST).locator("button.cf-mf-dl-one").inner_text() == "Retry")
    page.locator(f'.cf-mf-row[data-id="{MYST}"] .cf-mf-catsel').select_option("")
    row(page, MYST).locator("button.cf-mf-dl-one").click()          # Retry with no destination
    check("flash message displayed",
          "destination" in row(page, MYST).locator(".cf-mf-action").inner_text().lower(),
          row(page, MYST).locator(".cf-mf-action").inner_text())
    page.wait_for_timeout(2900)
    check("Retry restored after the flash message (row not left buttonless)",
          row(page, MYST).locator("button.cf-mf-dl-one").count() == 1
          and row(page, MYST).locator("button.cf-mf-dl-one").inner_text() == "Retry",
          row(page, MYST).locator(".cf-mf-action").inner_text())
    check("the original error message is shown again",
          "boom" in row(page, MYST).locator(".cf-mf-row-err").inner_text(),
          row(page, MYST).locator(".cf-mf-action").inner_text())

    # ---- 10. Several copies on disk: the user picks the one Relink writes ---
    # Two same-size copies in two subfolders, plus a third of the wrong size. The menu must
    # offer the first two only, and Relink must obey it rather than the automatic choice.
    MULTI = copy.deepcopy(ANALYZE)
    MULTI["models"][0]["local_matches"] = [
        {"path": "/m/checkpoints/Flux/flux1-dev.safetensors", "size": 100, "same_size": True,
         "value": "Flux/flux1-dev.safetensors", "root": "/m/checkpoints"},
        {"path": "/m/checkpoints/archive/flux1-dev.safetensors", "size": 100, "same_size": True,
         "value": "archive/flux1-dev.safetensors", "root": "/m/checkpoints"},
        {"path": "/m/checkpoints/old/flux1-dev.safetensors", "size": 42, "same_size": False,
         "value": "old/flux1-dev.safetensors", "root": "/m/checkpoints"},
        # Same relative path as the first, under a second registered root: ComfyUI resolves
        # both to the same widget value, so offering them separately would be a false choice.
        {"path": "/extra/checkpoints/Flux/flux1-dev.safetensors", "size": 100,
         "same_size": True, "value": "Flux/flux1-dev.safetensors", "root": "/extra/checkpoints"},
    ]
    page.evaluate("(p) => { window.api.payload = p; }", MULTI)
    page.evaluate("() => { window.app.extensionManager.workflow.activeWorkflow.key = 'workflow-P.json'; }")
    page.evaluate(GRAPH_A)
    page.evaluate("() => window.popup.openPopup()")
    page.wait_for_selector(".cf-mf-row")

    picker = row(page, FLUX).locator(".cf-mf-copysel")
    labels = picker.locator("option").all_inner_texts()
    check("picker offered when several copies exist", picker.count() == 1)
    # One root here, so the folder alone locates each copy; the filename is the row's title.
    check("only the same-size copies are listed, one entry per widget value",
          labels == ["Flux", "archive"], labels)
    check("the automatic choice is the one selected",
          picker.evaluate("el => el.selectedOptions[0].textContent") == "Flux")
    check("no picker on a row with a single copy",
          row(page, ORPHAN).locator(".cf-mf-copysel").count() == 0)

    picker.select_option(label="archive")
    check("the button follows the pick",
          "archive/flux1-dev.safetensors"
          in (row(page, FLUX).locator(".cf-mf-relink").get_attribute("title") or ""))
    row(page, FLUX).locator(".cf-mf-relink").click()
    check("the chosen copy is what lands in the node",
          page.evaluate("() => window.app.graph._nodes[0].widgets[0].value")
          == "archive/flux1-dev.safetensors")

    # Reopened, the popup reads the graph: the pick must not fall back to the automatic one.
    page.evaluate("() => window.popup.openPopup()")
    page.wait_for_selector(".cf-mf-row")
    check("the pick survives on the graph's word",
          row(page, FLUX).locator(".cf-mf-copysel")
          .evaluate("el => el.selectedOptions[0].textContent") == "archive")
    check("and the row reads as linked",
          row(page, FLUX).locator(".cf-mf-relinked-badge").count() == 1)

    # ---- 11. Telling apart copies that live on different disks -------------
    # Seen on a real install: two entries 60 characters long, identical but for the case of a
    # folder, and nothing saying which tree each one belonged to.
    NAME = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    DISKS = copy.deepcopy(ANALYZE)
    DISKS["models"] = [{**DISKS["models"][0], "id": "diffusion_models/" + NAME,
                        "filename": NAME, "category": "diffusion_models", "remote_size": 20,
                        "local_matches": [
        {"path": f"/opt/01_ComfyUI/models/unet/Minimax/{NAME}", "size": 20, "same_size": True,
         "value": f"Minimax/{NAME}", "root": "/opt/01_ComfyUI/models/unet"},
        {"path": f"/mnt/M/ComfyUI/models/diffusion_models/MiniMax/{NAME}", "size": 20,
         "same_size": True, "value": f"MiniMax/{NAME}",
         "root": "/mnt/M/ComfyUI/models/diffusion_models"},
    ], "relink": {"value": f"Minimax/{NAME}",
                  "path": f"/opt/01_ComfyUI/models/unet/Minimax/{NAME}",
                  "root": "/opt/01_ComfyUI/models/unet"}}]
    DISKS["categories"] = {"diffusion_models": {"target_dir": "/opt/01_ComfyUI/models/unet",
        "all_dirs": ["/opt/01_ComfyUI/models/unet", "/mnt/M/ComfyUI/models/diffusion_models"],
        "known": True, "subfolders": [""], "locations": []}}
    MX = "diffusion_models/" + NAME
    page.evaluate("(p) => { window.api.payload = p; }", DISKS)
    page.evaluate("() => { window.app.extensionManager.workflow.activeWorkflow.key = 'workflow-M.json'; }")
    page.evaluate("""() => { window.app.graph = { _nodes: [{ type: "UNETLoader",
      widgets: [{ name: "unet_name",
                  value: "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
                  options: { values: [] } }], setDirtyCanvas() {} }], setDirtyCanvas() {} }; }""")
    page.evaluate("() => window.popup.openPopup()")
    page.wait_for_selector(".cf-mf-row")

    opts = row(page, MX).locator(".cf-mf-copysel option").all_inner_texts()
    check("the filename is not repeated in every entry",
          all(NAME not in o for o in opts), opts)
    check("each entry names the root it belongs to",
          all(("unet" in o or "diffusion_models" in o) for o in opts), opts)
    check("two roots ending differently stay distinguishable", len(set(opts)) == 2, opts)
    check("and the subfolder is kept, case included",
          any(o.endswith("Minimax") for o in opts) and any(o.endswith("MiniMax") for o in opts),
          opts)
    titles = row(page, MX).locator(".cf-mf-copysel option").evaluate_all(
        "els => els.map((e) => e.title)")
    check("the full path stays one hover away",
          any("/mnt/M/" in t for t in titles) and any("/opt/01_ComfyUI/" in t for t in titles),
          titles)
    # Picking the copy on the other disk must still write ITS value, case included.
    row(page, MX).locator(".cf-mf-copysel").select_option(
        label=next(o for o in opts if o.endswith("MiniMax")))
    row(page, MX).locator(".cf-mf-relink").click()
    check("the copy chosen on the other disk is the one written",
          page.evaluate("() => window.app.graph._nodes[0].widgets[0].value") == "MiniMax/" + NAME)

    browser.close()

srv.shutdown()
print(f"\n{'FAILURES: ' + ', '.join(fails) if fails else 'All cases OK'}")
sys.exit(1 if fails else 0)
