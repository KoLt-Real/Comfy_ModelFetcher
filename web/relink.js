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
function norm(v) { return String(v).replace(/\\/g, "/").toLowerCase(); }

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
  const wanted = norm(value);
  const base = String(filename).toLowerCase();
  const candidates = [];
  for (const node of collectNodes(app.graph)) {
    const proxy = !!(node.subgraph || node.subgraphNode?.subgraph);
    for (const widget of node.widgets || []) {
      if (typeof widget.value !== "string" || !widget.value) continue;
      if (baseName(widget.value).toLowerCase() !== base) continue;
      const exact = comboValues(widget, node)
        .find((v) => typeof v === "string" && norm(v) === wanted);
      candidates.push({ node, widget, next: exact ?? value, known: !!exact, proxy });
    }
  }
  // A combo's list is specific to its category: if some widgets know the target value, those
  // are the right ones — same-named models of another category are dropped. Otherwise keep
  // them all (the node's list predates the file's arrival on disk).
  const known = candidates.filter((c) => c.known);
  return known.length ? known : candidates;
}

/**
 * Current state of the link in the open graph: { found, linked }.
 * Read again on every render rather than remembered — the graph is the source of truth (a
 * Ctrl+Z or a workflow switch shows up immediately).
 */
export function linkState(filename, value) {
  const wanted = norm(value);
  const counted = countedTargets(findTargets(filename, value));
  return {
    found: counted.length,
    linked: counted.filter((t) => norm(t.widget.value) === wanted).length,
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
    if (norm(t.widget.value) === norm(t.next)) { if (counted.has(t)) already++; continue; }
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
