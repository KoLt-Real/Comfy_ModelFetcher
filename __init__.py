"""ComfyFactory Model Fetcher — downloads the models listed in a workflow's Markdown notes.

Adds a button to ComfyUI's top bar; the popup reads the ``MarkdownNote`` nodes of the open
workflow, extracts the download URLs and destination folders, detects models that are already
installed / likely duplicates (name + size, extra paths included), and downloads the missing
ones to the right place with live progress.

HTTP API under ``/cf_mf``; progress pushed on the ComfyUI websocket (``cf_mf.*`` events).
Everything is wrapped defensively: an error here must never prevent ComfyUI from starting.
This package registers no nodes.
"""

import logging

logger = logging.getLogger("comfyfactory.modelfetcher")
logger.setLevel(logging.INFO)

WEB_DIRECTORY = "./web"


def _install() -> None:
    try:
        # Importing the routes module binds the @routes decorators to the PromptServer.
        from .fetcher import routes  # noqa: F401

        logger.info("ComfyFactory Model Fetcher: HTTP routes registered under /cf_mf")
    except Exception:
        logger.exception("ComfyFactory Model Fetcher: failed to register the routes")


_install()


# Empty V3 extension so ComfyUI does not complain about a missing node mapping.
try:
    from comfy_api.latest import ComfyExtension

    class _CFMFExtension(ComfyExtension):
        async def get_node_list(self):
            return []

    async def comfy_entrypoint() -> "ComfyExtension":
        return _CFMFExtension()
except Exception:
    NODE_CLASS_MAPPINGS = {}
    NODE_DISPLAY_NAME_MAPPINGS = {}
