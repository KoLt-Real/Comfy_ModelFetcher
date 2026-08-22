"""HuggingFace token panel E2E: state, saving, removal.

   python tests/e2e/test_token_panel.py   (needs: pip install playwright && playwright install chromium)
"""
import os, sys
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ANALYZE as _BASE, serve  # noqa: E402

# One gated model, no token: the guidance panel must show up on its own.
ANALYZE = dict(_BASE, hf_token=False, models=[dict(
    _BASE["models"][0], remote_size_error="401_gated", relink=None,
    url="https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/flux1-dev.safetensors")])

BASE, srv, _sandbox = serve()

fails = []
def ck(n, c, e=""):
    print(("OK   " if c else "FAIL ") + n + ("" if c else "  -> " + str(e)))
    if not c: fails.append(n)

with sync_playwright() as pw:
    b = pw.chromium.launch()
    pg = b.new_page()
    pg.on("pageerror", lambda e: ck("JS error: " + str(e), False))
    pg.goto(BASE + "/index.html")
    pg.wait_for_function("window.ready === true")
    pg.evaluate("(p) => { window.api.payload = p; }", ANALYZE)
    # capture the calls + a configurable answer for /cf_mf/token
    pg.evaluate("""() => {
      window.tokenState = { present: false, from_file: false };
      const real = window.api.fetchApi;
      window.api.fetchApi = async (url, opts) => {
        window.api.calls.push({url, opts});
        if (url === "/cf_mf/token" && (!opts || (opts.method||"GET") === "GET"))
          return { json: async () => ({ ok: true, ...window.tokenState }) };
        if (url === "/cf_mf/token" && opts && opts.method === "POST")
          return { json: async () => ({ ok: true, valid: true, username: "kolt" }) };
        if (url === "/cf_mf/token/clear") return { json: async () => ({ ok: true }) };
        return real(url, opts);
      };
    }""")
    pg.evaluate("() => { window.app.graph = { _nodes: [], setDirtyCanvas(){} }; }")
    pg.evaluate("() => window.popup.openPopup([{node_id:1,title:'n',text:'x'}])")
    pg.wait_for_selector(".cf-mf-row")

    # 1) gated model with no token -> guidance panel shown by default
    ck("token panel shown for a gated model", pg.locator(".cf-mf-token-panel").is_visible())
    ck("link to the gated repository offered", pg.locator(".cf-mf-token-repos a").count() >= 1)
    ck("the resolve/ URL is reduced to the repository page",
       pg.locator(".cf-mf-token-repos a").first.get_attribute("href")
       == "https://huggingface.co/black-forest-labs/FLUX.1-dev",
       pg.locator(".cf-mf-token-repos a").first.get_attribute("href"))
    ck("Remove hidden when no token is saved",
       pg.locator(".cf-mf-token-panel .cf-mf-token-forget").is_hidden())

    # 2) saving a token
    pg.locator(".cf-mf-token-input").first.fill("not-a-real-token")
    pg.locator(".cf-mf-token-save").first.click()
    pg.wait_for_timeout(300)
    ck("token sent to the server",
       pg.evaluate("() => window.api.calls.some(c => c.url === '/cf_mf/token' && c.opts?.method === 'POST')"))
    ck("confirmation shown with the account name",
       "kolt" in pg.locator(".cf-mf-token-status").first.inner_text(),
       pg.locator(".cf-mf-token-status").first.inner_text())

    # 3) on-demand panel (🔑 button) with a token ALREADY saved on disk
    pg.evaluate("() => { window.tokenState = { present: true, from_file: true }; }")
    pg.locator(".cf-mf-key").click()
    pg.wait_for_timeout(300)
    panel = pg.locator(".cf-mf-token-panel.cf-mf-token-ondemand")
    ck("on-demand panel opened", panel.is_visible())
    ck("Remove offered when a token is on disk",
       panel.locator(".cf-mf-token-forget").is_visible())

    # 4) removal
    panel.locator(".cf-mf-token-forget").click()
    pg.wait_for_timeout(300)
    ck("deletion called on the server",
       pg.evaluate("() => window.api.calls.some(c => c.url === '/cf_mf/token/clear')"))
    ck("Remove disappears after deletion", panel.locator(".cf-mf-token-forget").is_hidden())

    # 5) a token coming from the environment is NOT offered for deletion
    pg.locator(".cf-mf-key").click()  # closes it again
    pg.evaluate("() => { window.tokenState = { present: true, from_file: false }; }")
    pg.locator(".cf-mf-key").click()
    pg.wait_for_timeout(300)
    ck("environment token: no Remove button",
       pg.locator(".cf-mf-token-panel.cf-mf-token-ondemand .cf-mf-token-forget").is_hidden())
    b.close()

srv.shutdown()
print(f"\n{'FAILURES: ' + ', '.join(fails) if fails else 'token panel OK'}")
sys.exit(1 if fails else 0)
