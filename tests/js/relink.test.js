// Unit tests for web/relink.js against a fake ComfyUI graph.
//   node tests/js/relink.test.js
//
// web/relink.js imports "../../scripts/app.js" (a path served by ComfyUI). That tree is
// recreated in a temporary folder and the REAL source is copied into it on every run: there is
// never a stale copy to keep in sync inside the repo.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), "cf-mf-relink-"));
process.on("exit", () => fs.rmSync(sandbox, { recursive: true, force: true }));

fs.mkdirSync(path.join(sandbox, "scripts"), { recursive: true });
fs.mkdirSync(path.join(sandbox, "extensions", "mf"), { recursive: true });
fs.writeFileSync(path.join(sandbox, "package.json"), '{"type":"module"}');
fs.writeFileSync(path.join(sandbox, "scripts", "app.js"),
  "export const app = { graph: null, canvas: {}, dirty: 0 };\n");
fs.copyFileSync(path.join(repo, "web", "relink.js"),
                path.join(sandbox, "extensions", "mf", "relink.js"));

const { app } = await import(pathToFileURL(path.join(sandbox, "scripts", "app.js")).href);
const { relinkModel, linkState } =
  await import(pathToFileURL(path.join(sandbox, "extensions", "mf", "relink.js")).href);


let fails = 0;
const check = (name, cond, extra = "") => {
  console.log((cond ? "OK  " : "FAIL") + "  " + name + (cond ? "" : "   " + extra));
  if (!cond) fails++;
};
const widget = (nm, value, values) => ({ name: nm, value, options: values ? { values } : {} });
const node = (type, ws) => ({ type, widgets: ws, setDirtyCanvas() {} });

// --- Scenario 1: the node points at the root, the file lives in Flux/ ----------------
const loader = node("CheckpointLoaderSimple", [
  widget("ckpt_name", "flux1-dev.safetensors",
         ["sd15.safetensors", "Flux/flux1-dev.safetensors"]),
]);
// same name in ANOTHER category: its list does not hold the target -> leave it alone
const vae = node("VAELoader", [
  widget("vae_name", "flux1-dev.safetensors", ["ae.safetensors"]),
]);
// the same file used inside a subgraph
const inner = node("CheckpointLoaderSimple", [
  widget("ckpt_name", "flux1-dev.safetensors", ["Flux/flux1-dev.safetensors"]),
]);
const sgNode = { type: "Subgraph", widgets: [], subgraph: { _nodes: [inner] } };
// node already correct
const already = node("CheckpointLoaderSimple", [
  widget("ckpt_name", "Flux/flux1-dev.safetensors", ["Flux/flux1-dev.safetensors"]),
]);
app.graph = { _nodes: [loader, vae, sgNode, already], setDirtyCanvas() { app.dirty++; } };

let r = relinkModel("flux1-dev.safetensors", "Flux/flux1-dev.safetensors");
check("3 widgets targeted (the VAE namesake dropped)", r.found === 3, JSON.stringify(r));
check("2 nodes fixed", r.changed === 2, JSON.stringify(r));
check("1 node already right", r.already === 1, JSON.stringify(r));
check("values from the list reused as-is", !r.unverified, JSON.stringify(r));
check("loader fixed", loader.widgets[0].value === "Flux/flux1-dev.safetensors", loader.widgets[0].value);
check("subgraph node fixed", inner.widgets[0].value === "Flux/flux1-dev.safetensors", inner.widgets[0].value);
check("the VAE namesake untouched", vae.widgets[0].value === "flux1-dev.safetensors", vae.widgets[0].value);
check("canvas invalidated", app.dirty === 1);

// --- Scenario 2: stale node list (the file arrived after the page was loaded) ---------
const stale = node("CheckpointLoaderSimple", [
  widget("ckpt_name", "wan2.2.safetensors", ["other.safetensors"]),
]);
app.graph = { _nodes: [stale], setDirtyCanvas() {} };
r = relinkModel("wan2.2.safetensors", "WAN/wan2.2.safetensors");
check("falls back on the only candidate", r.found === 1 && r.changed === 1, JSON.stringify(r));
check("reported as unverified", r.unverified === true, JSON.stringify(r));
check("value written", stale.widgets[0].value === "WAN/wan2.2.safetensors", stale.widgets[0].value);
check("value added to the combo list",
      stale.widgets[0].options.values.includes("WAN/wan2.2.safetensors"),
      JSON.stringify(stale.widgets[0].options.values));

// --- Scenario 3: Windows separators + non-string values + no node at all --------------
const win = node("CheckpointLoaderSimple", [
  widget("ckpt_name", "flux1-dev.safetensors", ["Flux\\flux1-dev.safetensors"]),
  { name: "seed", value: 42, options: {} },
  { name: "dyn", value: "flux1-dev.safetensors", options: { values: () => ["Flux/flux1-dev.safetensors"] } },
]);
app.graph = { _nodes: [win], setDirtyCanvas() {} };
r = relinkModel("flux1-dev.safetensors", "Flux/flux1-dev.safetensors");
check("numeric widget ignored", r.found === 2, JSON.stringify(r));
check("Windows path from the list kept as-is",
      win.widgets[0].value === "Flux\\flux1-dev.safetensors", win.widgets[0].value);
check("a function options.values is supported",
      win.widgets[2].value === "Flux/flux1-dev.safetensors", win.widgets[2].value);

app.graph = { _nodes: [node("KSampler", [widget("sampler", "euler", ["euler"])])], setDirtyCanvas() {} };
r = relinkModel("flux1-dev.safetensors", "Flux/flux1-dev.safetensors");
check("no node concerned -> found 0", r.found === 0 && r.changed === 0, JSON.stringify(r));


// --- Scenario 4: linkState reflects the real state of the graph -----------------------
const l1 = node("CheckpointLoaderSimple", [widget("ckpt_name", "a.safetensors", ["Flux/a.safetensors"])]);
const l2 = node("CheckpointLoaderSimple", [widget("ckpt_name", "Flux/a.safetensors", ["Flux/a.safetensors"])]);
app.graph = { _nodes: [l1, l2], setDirtyCanvas() {} };
let st = linkState("a.safetensors", "Flux/a.safetensors");
check("linkState: 2 found, 1 already linked", st.found === 2 && st.linked === 1, JSON.stringify(st));
relinkModel("a.safetensors", "Flux/a.safetensors");
st = linkState("a.safetensors", "Flux/a.safetensors");
check("linkState after relink: all linked", st.found === 2 && st.linked === 2, JSON.stringify(st));
app.graph = { _nodes: [], setDirtyCanvas() {} };
st = linkState("a.safetensors", "Flux/a.safetensors");
check("linkState on an empty graph", st.found === 0 && st.linked === 0, JSON.stringify(st));


// --- Scenario 5: loader inside a subgraph with a promoted widget (the "Relink ×2" bug) -
// The container node carries the promotion of the inner loader's widget: it must be rewritten
// but counted ONCE.
const innerLoader = node("CheckpointLoaderSimple", [
  widget("ckpt_name", "sg.safetensors", ["Flux/sg.safetensors"]),
]);
const container = {
  type: "a1b2c3-subgraph-uuid",
  widgets: [widget("ckpt_name", "sg.safetensors", ["Flux/sg.safetensors"])], // the promotion
  subgraph: { _nodes: [innerLoader] },
  setDirtyCanvas() {},
};
app.graph = { _nodes: [container], setDirtyCanvas() {} };
st = linkState("sg.safetensors", "Flux/sg.safetensors");
check("subgraph: linkState counts 1 (not 2)", st.found === 1 && st.linked === 0, JSON.stringify(st));
let r5 = relinkModel("sg.safetensors", "Flux/sg.safetensors");
check("subgraph: relink counts 1 change", r5.found === 1 && r5.changed === 1, JSON.stringify(r5));
check("subgraph: inner widget rewritten",
      innerLoader.widgets[0].value === "Flux/sg.safetensors", innerLoader.widgets[0].value);
check("subgraph: promoted widget rewritten too",
      container.widgets[0].value === "Flux/sg.safetensors", container.widgets[0].value);
st = linkState("sg.safetensors", "Flux/sg.safetensors");
check("subgraph: all linked after relink", st.found === 1 && st.linked === 1, JSON.stringify(st));

// Only the proxy still matches (inner already relinked, promotion left behind):
const inner2 = node("CheckpointLoaderSimple", [
  widget("ckpt_name", "Flux/pp.safetensors", ["Flux/pp.safetensors"]),
]);
const cont2 = {
  type: "uuid2", subgraph: { _nodes: [inner2] }, setDirtyCanvas() {},
  widgets: [widget("ckpt_name", "pp.safetensors", ["Flux/pp.safetensors"])],
};
app.graph = { _nodes: [cont2], setDirtyCanvas() {} };
r5 = relinkModel("pp.safetensors", "Flux/pp.safetensors");
check("proxy alone: rewritten (wrote) but counted as already linked", r5.found === 1 && r5.changed === 0
      && r5.already === 1 && r5.wrote === true
      && cont2.widgets[0].value === "Flux/pp.safetensors", JSON.stringify(r5));

// Two real loaders (outside a subgraph): the count stays 2 — no over-correction.
const lA = node("CheckpointLoaderSimple", [widget("ckpt_name", "two.safetensors", ["F/two.safetensors"])]);
const lB = node("CheckpointLoaderSimple", [widget("ckpt_name", "two.safetensors", ["F/two.safetensors"])]);
app.graph = { _nodes: [lA, lB], setDirtyCanvas() {} };
r5 = relinkModel("two.safetensors", "F/two.safetensors");
check("2 real loaders: counts 2", r5.found === 2 && r5.changed === 2, JSON.stringify(r5));

console.log(fails ? `\n${fails} failure(s)` : "\nAll cases OK");
process.exit(fails ? 1 : 0);
