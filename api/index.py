"""Vercel entrypoint: serves the FastAPI catalog from `server.py` as a Python function.

Vercel's Python runtime looks for an ASGI app named `app` in `api/*.py`, so this module's only
jobs are to put the repo root on the import path and to load the catalog eagerly. The load is
explicit rather than left to FastAPI's startup event because a serverless invocation does not
reliably run lifespan hooks — and a server that silently starts with an empty catalog is exactly
the "plausible but wrong" failure the extractor itself refuses to produce.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

server._CATALOG = server.load_catalog()

app = server.app

# Routing note (vercel.json cannot carry comments):
#   `/api/(.*)` -> `/api/index` sends every API path to this one function. A Vercel rewrite
#   only *selects* the handler; the request keeps its original path, so FastAPI's own routes
#   still match and no root_path juggling is needed. The second rule sends everything else to
#   the SPA shell for client-side routing.
