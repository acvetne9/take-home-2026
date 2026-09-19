# `api/`

One file, adapting `server.py` to Vercel's Python runtime, which looks for an ASGI app named
`app` in `api/*.py`. **Keep it thin — application logic belongs in `server.py`.** No extraction
code, no network calls, no write paths.

## Two decisions worth not undoing

- **Load the catalog eagerly at import, not in a FastAPI startup event.** Serverless
  invocations do not reliably run lifespan hooks, and a server that starts with an empty catalog
  fails silently.
- **The rewrite selects a handler; it does not rewrite the path.** `vercel.json` sends
  `/api/(.*)` to this function and the request keeps its original path, so FastAPI's own routes
  match and no `root_path` juggling is needed. A second rule sends everything else to the SPA
  shell. `vercel.json` cannot carry comments, so that note lives in the module docstring — keep
  it in sync if routing changes.

## Before deploying

The deployed site serves `out/products.json` as-is, so confirm it came from a real keyed run.
