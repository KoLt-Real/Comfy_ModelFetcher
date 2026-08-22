import { app } from "../../scripts/app.js";

// ---------------------------------------------------------------------------
// Relink: point the loader nodes of the open workflow at the copy that already
// sits on disk.
//
// ComfyUI templates point at the root of the category ("flux1-dev.safetensors")
// while the file is tucked inside a subfolder ("Flux/flux1-dev.safetensors"):
// ComfyUI lists models as paths relative to the category folder, so the widget
// value is what has to be fixed.
// ---------------------------------------------------------------------------

function baseName(v) { return String(v).split(/[/\\]/).pop(); }
// Separators only. Case is NOT folded: two copies of one model can differ by the case of a
// folder alone, and on a case-sensitive filesystem those are two different files.
function slashed(v) { return String(v).replace(/\\/g, "/"); }
// Case folded on top, for the Windows tolerance: same file, other spelling.
function norm(v) { return slashed(v).toLowerCase(); }

// Nodes of the open graph, subgraphs included (same traversal as the note collection).
function collectNodes(graph, out = [], seen = new Set()) {
  if (!graph || seen.has(graph)) return out;
  seen.add(graph);
  for (const node of graph._nodes || graph.nodes || []) {
    if (!seen.has(node)) { seen.add(node); out.push(node); }
    const sub = node.subgraph || node.subgraphNode?.subgraph;
    if (sub) collectNodes(sub, out, seen);
  }
  if (graph.subgraphs?.forEach) graph.subgraphs.forEach((sg) => collectNodes(sg, out, seen));
  return out;
}

// Values offered by a combo widget (fixed list or function).
function comboValues(widget, node) {
  const v = widget.options?.values;
  if (Array.isArray(v)) return v;
  if (typeof v === "function") {
    try {
      const r = v(widget, node);
      return Array.isArray(r) ? r : [];
    } catch (e) { return []; }
  }
  return [];
}

/**
 * Widgets of the workflow that designate this file.
 * `value` = the relative path aimed at (e.g. "Flux/flux1-dev.safetensors").
 *
 * A widget carried by a subgraph container node is the promotion of a widget of an inner node
 * (found by the traversal as well): marked `proxy`, it does get rewritten but does not count
 * as an extra target — otherwise a loader inside a subgraph would count twice.
 */
export function findTargets(filename, value) {
  return resolve(widgetsFor(filename), value);
}

// Widgets of the open graph that designate this file, whatever they point at. Split out
// because it is the expensive half — it walks the graph and every subgraph — and answering
// "which of these N copies is the graph on?" must not pay for it N times.
function widgetsFor(filename) {
  const base = String(filename).toLowerCase();
  const found = [];
  for (const node of collectNodes(app.graph)) {
    const proxy = !!(node.subgraph || node.subgraphNode?.subgraph);
    for (const widget of node.widgets || []) {
      if (typeof widget.value !== "string" || !widget.value) continue;
      if (baseName(widget.value).toLowerCase() !== base) continue;
      found.push({ node, widget, proxy });
    }
  }
  return found;
}

// Which of those widgets designate THIS value, and what to write into each. Walks combo lists
// only — no graph traversal.
function resolve(widgets, value) {
  const wanted = slashed(value);
  const candidates = widgets.map((w) => {
    const values = comboValues(w.widget, w.node);
    // Byte-exact lookup decides what gets WRITTEN. Falling through to the case-folded match
    // there would rewrite "MiniMax/model.safetensors" as "Minimax/model.safetensors" whenever
    // the node's list happens to hold the other spelling — writing the copy the user did NOT
    // pick, while the button's tooltip promised theirs. So the widget always receives the
    // exact spelling of the pick, never a variant from the list.
    const exact = values.find((v) => typeof v === "string" && slashed(v) === wanted);
    // The case-folded match decides only KNOWN-ness — "does this widget belong to the value's
    // category?". On Windows a list can legitimately spell the same file differently, and a
    // variant proves membership just as well. Both sides folded: comparing a folded entry to
    // the unfolded value made this test unpassable for any mixed-case value, which silently
    // disabled the category filter below.
    const known = !!exact
      || values.some((v) => typeof v === "string" && norm(v) === norm(wanted));
    return { ...w, next: exact ?? value, known };
  });
  // A combo's list is specific to its category: if some widgets know the target value, those
  // are the right ones — same-named models of another category are dropped. Otherwise keep
  // them all (the node's list predates the file's arrival on disk).
  const known = candidates.filter((c) => c.known);
  return known.length ? known : candidates;
}

/**
 * Which of `values` the graph is ALREADY fully pointing at, or null.
 *
 * One traversal for the whole list. Asking `linkState()` once per candidate re-walked the
 * graph, and every subgraph, once per candidate — on the path that runs for every render of
 * every row.
 */
export function linkedAmong(filename, values) {
  const widgets = widgetsFor(filename);
  if (!widgets.length) return null;
  for (const value of values) {
    const counted = countedTargets(resolve(widgets, value));
    if (counted.length && counted.every((t) => slashed(t.widget.value) === slashed(value))) {
      return value;
    }
  }
  return null;
}

/**
 * Current state of the link in the open graph: { found, linked }.
 * Read again on every render rather than remembered — the graph is the source of truth (a
 * Ctrl+Z or a workflow switch shows up immediately).
 */
export function linkState(filename, value) {
  const wanted = slashed(value);
  const counted = countedTargets(findTargets(filename, value));
  return {
    found: counted.length,
    // Compared exactly, like `linkedAmong`: claiming a row is linked to a copy it merely
    // resembles would name the wrong disk in the badge — and invite deleting the right file.
    linked: counted.filter((t) => slashed(t.widget.value) === wanted).length,
  };
}

// Counted targets: the widgets of leaf nodes. When only proxies match (inner widget already
// relinked but the promotion left behind), those are counted instead.
function countedTargets(targets) {
  const leaves = targets.filter((t) => !t.proxy);
  return leaves.length ? leaves : targets;
}

/**
 * Fix the widgets pointing at this file.
 * → { found, changed, already, unverified, wrote }
 * `changed`/`already` count the logical targets (subgraph proxies excluded);
 * `wrote` says whether the graph was modified (proxies included) — that is what triggers the
 * canvas invalidation and the save reminder.
 */
export function relinkModel(filename, value) {
  const targets = findTargets(filename, value);
  const counted = new Set(countedTargets(targets));
  let changed = 0, already = 0, unverified = false, wrote = false;

  for (const t of targets) {
    if (slashed(t.widget.value) === slashed(t.next)) { if (counted.has(t)) already++; continue; }
    setWidgetValue(t.node, t.widget, t.next);
    wrote = true;
    if (!counted.has(t)) continue; // proxy rewritten but not counted
    if (!t.known) unverified = true;
    changed++;
  }

  if (wrote) {
    try { app.graph.setDirtyCanvas(true, true); } catch (e) { /* noop */ }
    // Flag the change (the "unsaved" dot) when the API is available.
    try { app.graph.change?.(); } catch (e) { /* noop */ }
    try {
      app.extensionManager?.workflow?.activeWorkflow?.changeTracker?.checkState?.();
    } catch (e) { /* noop */ }
  }

  return { found: counted.size, changed, already, unverified, wrote };
}

function setWidgetValue(node, widget, value) {
  // The combo must be able to display the value: when the node's list predates the file's
  // arrival, add it there (a refresh of the definitions would find it anyway).
  if (Array.isArray(widget.options?.values) && !widget.options.values.includes(value)) {
    widget.options.values = [...widget.options.values, value];
  }
  const previous = widget.value;
  widget.value = value;
  try { widget.callback?.(value, app.canvas, node, undefined, undefined); } catch (e) { /* noop */ }
  try { node.onWidgetChanged?.(widget.name, value, previous, widget); } catch (e) { /* noop */ }
}
