import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { isPopupOpen, onGraphConfigured, openPopup, wireEvents } from "./popup.js";

// Load the stylesheet (WEB_DIRECTORY only auto-imports the JS).
(function loadCss() {
  if (document.getElementById("cf-mf-style")) return;
  const link = document.createElement("link");
  link.id = "cf-mf-style";
  link.rel = "stylesheet";
  link.href = new URL("style.css", import.meta.url).href;
  document.head.appendChild(link);
})();

// ---------------------------------------------------------------------------
// Collection of the open workflow's MarkdownNote nodes (subgraphs included).
// ---------------------------------------------------------------------------
function collectNotes(graph, out = [], seen = new Set()) {
  if (!graph || seen.has(graph)) return out;
  seen.add(graph);
  const nodes = graph._nodes || graph.nodes || [];
  for (const node of nodes) {
    const type = node.type || node.comfyClass || "";
    if (type === "MarkdownNote" || type === "Note") {
      let text = "";
      try {
        text = node.widgets?.[0]?.value ?? node.widgets_values?.[0] ?? "";
      } catch (e) { /* noop */ }
      if (typeof text === "string" && text.trim()) {
        out.push({ node_id: node.id, title: node.title || "", text });
      }
    }
    // Recursive descent into subgraphs.
    const sub = node.subgraph || node.subgraphNode?.subgraph;
    if (sub) collectNotes(sub, out, seen);
  }
  if (graph.subgraphs?.forEach) graph.subgraphs.forEach((sg) => collectNotes(sg, out, seen));
  return out;
}

export function gatherWorkflowNotes() {
  return collectNotes(app.graph);
}

// ---------------------------------------------------------------------------
// Badge: how many models are still to download, refreshed live.
// The /cf_mf/count endpoint is light (no network request), safe to call often.
// ---------------------------------------------------------------------------
let buttonEl = null;
let badgeTimer = null;
let badgeSeq = 0;

export function setBadge(count) {
  if (!buttonEl) return;
  const b = buttonEl.querySelector(".cf-mf-btn-badge");
  if (!b) return;
  if (count > 0) { b.textContent = String(count); b.hidden = false; }
  else { b.textContent = "0"; b.hidden = true; }
}

async function refreshBadge() {
  const seq = ++badgeSeq;
  try {
    const notes = gatherWorkflowNotes();
    const resp = await api.fetchApi("/cf_mf/count", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes }),
    });
    const data = await resp.json();
    if (seq !== badgeSeq) return; // a more recent request was fired meanwhile
    setBadge(data.ok ? (data.missing || 0) : 0);
  } catch (e) {
    if (seq === badgeSeq) setBadge(0);
  }
}

// Debounce: coalesces bursts of events (workflow load, note editing…).
export function scheduleBadgeRefresh(delay = 350) {
  clearTimeout(badgeTimer);
  badgeTimer = setTimeout(refreshBadge, delay);
}

// ---------------------------------------------------------------------------
// Button: dockable in the actionbar (top bar) OR floating/draggable.
// Persistent state: { docked, x, y } in localStorage.
// ---------------------------------------------------------------------------
const BTN_TAG = "cf-mf-button";
const LS_KEY = "cf_mf.button.state";
const DRAG_THRESHOLD = 5; // px before this counts as a drag (rather than a click)
let isDocked = true;

function loadState() {
  try { return JSON.parse(localStorage.getItem(LS_KEY)) || {}; } catch (e) { return {}; }
}
function saveState(patch) {
  const s = { ...loadState(), ...patch };
  try { localStorage.setItem(LS_KEY, JSON.stringify(s)); } catch (e) { /* noop */ }
  return s;
}

if (!customElements.get(BTN_TAG)) {
  customElements.define(
    BTN_TAG,
    class extends HTMLElement {
      disconnectedCallback() {
        if (this._onDisconnect) queueMicrotask(() => this._onDisconnect());
      }
    }
  );
}

function getActionbarContainer() {
  return document.querySelector(".actionbar-container") || document.querySelector(".comfyui-menu");
}

function buildButton() {
  const el = document.createElement(BTN_TAG);
  el.className = "cf-mf-topbar-btn";
  el.title = "Model Fetcher — click: open · drag: move / (un)dock";
  el.innerHTML = `
    <span class="cf-mf-btn-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>
      </svg>
    </span>
    <span class="cf-mf-btn-label">Models</span>
    <span class="cf-mf-btn-badge" hidden>0</span>`;
  el._onDisconnect = () => { if (isDocked) redock(); };
  attachDrag(el);
  return el;
}

function redock() {
  const ac = getActionbarContainer();
  if (!ac || !buttonEl) return;
  if (buttonEl.parentNode !== ac) ac.appendChild(buttonEl);
}

function dockButton() {
  const ac = getActionbarContainer();
  if (!ac || !buttonEl) return;
  isDocked = true;
  buttonEl.classList.remove("cf-mf-floating", "cf-mf-will-dock");
  buttonEl.style.left = "";
  buttonEl.style.top = "";
  buttonEl.style.position = "";
  buttonEl.style.zIndex = "";
  ac.appendChild(buttonEl);
  saveState({ docked: true });
}

function undockButtonAt(x, y) {
  isDocked = false;
  buttonEl.classList.add("cf-mf-floating");
  document.body.appendChild(buttonEl);
  placeFloating(x, y);
  saveState({ docked: false });
}

function placeFloating(x, y) {
  const w = buttonEl.offsetWidth || 90;
  const h = buttonEl.offsetHeight || 28;
  const cx = Math.max(2, Math.min(window.innerWidth - w - 2, x));
  const cy = Math.max(2, Math.min(window.innerHeight - h - 2, y));
  buttonEl.style.position = "fixed";
  buttonEl.style.left = cx + "px";
  buttonEl.style.top = cy + "px";
  buttonEl.style.zIndex = "10001";
  saveState({ x: cx, y: cy });
}

// Is the cursor over the docking bar (with a tolerance)?
function isOverDock(clientX, clientY) {
  const ac = getActionbarContainer();
  if (!ac) return false;
  const r = ac.getBoundingClientRect();
  const pad = 24;
  return clientX >= r.left - pad && clientX <= r.right + pad &&
         clientY >= r.top - pad && clientY <= r.bottom + pad;
}

function attachDrag(el) {
  let startX = 0, startY = 0, grabX = 0, grabY = 0;
  let moved = false;

  const onMove = (e) => {
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    if (!moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
    moved = true;
    el.classList.add("cf-mf-dragging");
    // First time the threshold is crossed from the docked state → undock.
    if (isDocked) {
      const r = el.getBoundingClientRect();
      grabX = e.clientX - r.left;
      grabY = e.clientY - r.top;
      undockButtonAt(e.clientX - grabX, e.clientY - grabY);
    }
    placeFloating(e.clientX - grabX, e.clientY - grabY);
    el.classList.toggle("cf-mf-will-dock", isOverDock(e.clientX, e.clientY));
  };

  const onUp = (e) => {
    window.removeEventListener("mousemove", onMove, true);
    window.removeEventListener("mouseup", onUp, true);
    el.classList.remove("cf-mf-dragging", "cf-mf-will-dock");
    if (!moved) {
      // plain click → open the popup
      openPopup(gatherWorkflowNotes());
      return;
    }
    // end of drag: (re)dock when dropped on the bar, otherwise stay floating
    if (isOverDock(e.clientX, e.clientY)) dockButton();
  };

  el.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    moved = false;
    startX = e.clientX;
    startY = e.clientY;
    if (!isDocked) {
      const r = el.getBoundingClientRect();
      grabX = e.clientX - r.left;
      grabY = e.clientY - r.top;
    }
    window.addEventListener("mousemove", onMove, true);
    window.addEventListener("mouseup", onUp, true);
  });

  // Keyboard: Enter/Space opens the popup (accessibility).
  el.tabIndex = 0;
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openPopup(gatherWorkflowNotes());
    }
  });
}

// Bring a floating button back on screen after a resize.
window.addEventListener("resize", () => {
  if (!isDocked && buttonEl) {
    const s = loadState();
    placeFloating(s.x ?? 40, s.y ?? 40);
  }
});

function mountButton() {
  if (!buttonEl) buttonEl = buildButton();
  const s = loadState();
  if (s.docked === false) {
    isDocked = false;
    buttonEl.classList.add("cf-mf-floating");
    document.body.appendChild(buttonEl);
    placeFloating(s.x ?? 40, s.y ?? 40);
  } else {
    isDocked = true;
    redock();
  }
}

function waitForMount(tries = 0) {
  // Floating mode can mount right away; docked mode waits for the actionbar.
  const s = loadState();
  if (s.docked === false || getActionbarContainer()) {
    mountButton();
    return;
  }
  if (tries > 300) { mountButton(); return; } // safety net
  requestAnimationFrame(() => waitForMount(tries + 1));
}

app.registerExtension({
  name: "NE.modelfetcher",
  // Fires on workflow load AND on tab switch (the graph gets reconfigured).
  afterConfigureGraph() {
    onGraphConfigured();
    scheduleBadgeRefresh(250);
    // Popup left open across a workflow switch: re-analyse the new graph, otherwise we would
    // display — and relink — the state of another workflow.
    if (isPopupOpen()) setTimeout(() => openPopup(gatherWorkflowNotes()), 200);
  },
  async setup() {
    wireEvents(api);
    // Refresh the badge when a download finishes (the counter drops live).
    api.addEventListener("cf_mf.done", () => scheduleBadgeRefresh(150));
    waitForMount();
    scheduleBadgeRefresh(1200); // first count, once the initial graph has settled
  },
});
