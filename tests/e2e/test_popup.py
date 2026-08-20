"""Popup E2E: Relink, preference persistence, isolation between workflows.

   python tests/e2e/test_popup.py   (needs: pip install playwright && playwright install chromium)
"""
import os, sys
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
    check("relink refused on a workflow that changed",
          "Workflow changed" in row(page, FLUX).locator(".cf-mf-action").inner_text(),
          row(page, FLUX).locator(".cf-mf-action").inner_text())
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

    browser.close()

srv.shutdown()
print(f"\n{'FAILURES: ' + ', '.join(fails) if fails else 'All cases OK'}")
sys.exit(1 if fails else 0)
