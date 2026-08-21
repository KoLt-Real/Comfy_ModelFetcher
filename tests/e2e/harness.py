"""Serves the repo's real web/ inside a fake ComfyUI tree, for Playwright.

popup.js imports "../../scripts/{app,api}.js": that level is recreated with stubs and web/ is
copied on every run — the tests always exercise the current source.
"""
from __future__ import annotations

import functools
import http.server
import os
import shutil
import socketserver
import tempfile
import threading

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

APP_STUB = """export const app = {
  graph: { _nodes: [], setDirtyCanvas() {} },
  canvas: {},
  extensionManager: { workflow: { activeWorkflow: { key: "workflow-A.json" } } },
};
"""

API_STUB = """export const api = {
  payload: null,
  calls: [],
  delayMs: 0,            // delay of the NEXT analysis
  statusPayload: { ok: true, pending: [], active: null },
  statusDelayMs: 0,      // delay of the NEXT /status answer
  _handlers: {},
  addEventListener(type, fn) { (api._handlers[type] ||= []).push(fn); },
  emit(type, detail) { for (const fn of api._handlers[type] || []) fn({ detail }); },
  async fetchApi(url, opts) {
    api.calls.push({ url, opts });
    let data = { ok: true };
    if (url.startsWith("/cf_mf/analyze")) {
      data = api.payload;
      const d = api.delayMs; api.delayMs = 0;
      if (d) await new Promise((r) => setTimeout(r, d));
    } else if (url.startsWith("/cf_mf/status")) {
      data = api.statusPayload;
      const d = api.statusDelayMs; api.statusDelayMs = 0;
      if (d) await new Promise((r) => setTimeout(r, d));
    }
    return { json: async () => data };
  },
};
"""

INDEX = """<!doctype html><meta charset="utf-8"><title>model fetcher test</title>
<link rel="stylesheet" href="/extensions/mf/style.css">
<body>
<script type="module">
import * as popup from "/extensions/mf/popup.js";
import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";
popup.wireEvents(api);            // exactly what main.js does at startup
window.popup = popup; window.app = app; window.api = api; window.ready = true;
</script>
"""


def serve() -> tuple[str, socketserver.TCPServer, str]:
    """Set up the sandbox and start a server. → (base_url, server, folder)."""
    root = tempfile.mkdtemp(prefix="cf-mf-e2e-")
    os.makedirs(os.path.join(root, "scripts"))
    os.makedirs(os.path.join(root, "extensions", "mf"))
    for name in ("popup.js", "relink.js", "style.css"):
        shutil.copy(os.path.join(REPO, "web", name), os.path.join(root, "extensions", "mf", name))
    open(os.path.join(root, "scripts", "app.js"), "w").write(APP_STUB)
    open(os.path.join(root, "scripts", "api.js"), "w").write(API_STUB)
    open(os.path.join(root, "index.html"), "w").write(INDEX)

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=root)
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", srv, root


# A typical analysis response, shared by the E2E tests.
ANALYZE = {'ok': True,
 'models': [{'id': 'checkpoints/flux1-dev.safetensors',
             'filename': 'flux1-dev.safetensors',
             'url': 'https://hf/flux1-dev.safetensors',
             'category': 'checkpoints',
             'category_known': True,
             'status': 'duplicate_same_size',
             'remote_size': 100,
             'remote_size_error': None,
             'local_matches': [{'path': '/m/checkpoints/Flux/flux1-dev.safetensors',
                                'size': 100,
                                'same_size': True,
                                'value': 'Flux/flux1-dev.safetensors',
                                'root': '/m/checkpoints'}],
             'other_category_matches': [],
             'source_note': '',
             'relink': {'value': 'Flux/flux1-dev.safetensors',
                        'path': '/m/checkpoints/Flux/flux1-dev.safetensors',
                        'root': '/m/checkpoints'}},
            {'id': 'checkpoints/tobedl.safetensors',
             'filename': 'tobedl.safetensors',
             'url': 'https://hf/tobedl.safetensors',
             'category': 'checkpoints',
             'category_known': True,
             'status': 'missing',
             'remote_size': 200,
             'remote_size_error': None,
             'local_matches': [],
             'other_category_matches': [],
             'source_note': '',
             'relink': None},
            {'id': 'vae/orphan.safetensors',
             'filename': 'orphan.safetensors',
             'url': 'https://hf/orphan.safetensors',
             'category': 'vae',
             'category_known': True,
             'status': 'duplicate_same_size',
             'remote_size': 50,
             'remote_size_error': None,
             'local_matches': [{'path': '/m/vae/sub/orphan.safetensors',
                                'size': 50,
                                'same_size': True,
                                'value': 'sub/orphan.safetensors',
                                'root': '/m/vae'}],
             'other_category_matches': [],
             'source_note': '',
             'relink': {'value': 'sub/orphan.safetensors',
                        'path': '/m/vae/sub/orphan.safetensors',
                        'root': '/m/vae'}},
            {'id': '?/mystery.safetensors',
             'filename': 'mystery.safetensors',
             'url': 'https://hf/mystery.safetensors',
             'category': None,
             'category_known': False,
             'status': 'unknown_dest',
             'remote_size': 10,
             'remote_size_error': None,
             'local_matches': [],
             'other_category_matches': [],
             'source_note': '',
             'relink': None}],
 'categories': {'checkpoints': {'target_dir': '/m/checkpoints',
                                'all_dirs': ['/m/checkpoints'],
                                'known': True,
                                'subfolders': ['', 'Flux'],
                                'locations': [{'dir': '/m/checkpoints',
                                               'exists': True,
                                               'is_default': True,
                                               'subfolders': ['', 'Flux']}]},
                'vae': {'target_dir': '/m/vae',
                        'all_dirs': ['/m/vae'],
                        'known': True,
                        'subfolders': ['', 'sub'],
                        'locations': [{'dir': '/m/vae',
                                       'exists': True,
                                       'is_default': True,
                                       'subfolders': ['', 'sub']}]}},
 'known_categories': ['checkpoints', 'vae'],
 'hf_token': True}
