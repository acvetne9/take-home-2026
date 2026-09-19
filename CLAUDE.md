# Product extraction

Raw product-detail HTML in, validated `Product` records out, plus a catalog UI over the result.

<!-- Loads on every session. Keep it to rules that apply everywhere. Area detail belongs in
     extract/, tests/, web/, data/, api/CLAUDE.md, which load on demand, and in
     .claude/rules/core-modules.md for the root-level modules. Do not restate those here. -->

**Design premise:** a product page is a document already rendered from a database, and most of
the database is still in the file. Parse everything the page states; ask a model only what
nobody stated.

## Invariants

These are structural, so they outrank every other consideration.

1. **No site- or page-specific logic.** No `if domain == "…"`, no selector targeting a known
   attribute on a known site. Comments count.
2. **No page-specific hints in prompts.** Generic few-shot is fine; few-shot drawn from the
   pages in `data/` is not.

Corollary: **every heuristic must cite a published convention** — schema.org, Open Graph, W3C
Microdata/RDFa, framework hydration, the HTML responsive-images spec, CDN rendition
conventions. A rule that cannot name its convention is a site rule in disguise.

`tests/test_extract.py::test_no_site_specific_logic` enforces this over the AST and raw text of
every module. Never weaken it to make something pass.

## Never

- Commit `.env`.
- Modify `ai.py`. It is a vendored client wrapper, treated as a fixed dependency.
- Publish a plausible value in place of a missing one. **A wrong row is worse than a missing
  one:** a missing row shows up in a dashboard, a $0.00 row canonicalises against a real product
  and corrupts it. When adding any path, ask *what wrong answer could this produce silently*,
  not *does it parse*.

## Commands

```bash
uv sync                       # or: pip install -e '.[dev]'
cp .env.example .env          # add OPEN_ROUTER_API_KEY

python main.py                # extract ./data/*.html -> ./out/products.json
pytest                        # 72 tests, no network

python server.py              # FastAPI on :8000  (docs at /docs)
cd web && npm install && npm run dev      # Vite on :5173, proxies /api to :8000
```

`main.py` also takes `--model`, `--no-cascade`, and explicit paths to run one page.

## Layout

| path | what it is |
|---|---|
| `extract/` | the pipeline: deterministic stages, then one model call |
| `tests/` | the regression suite |
| `web/` | Vite + React catalog and PDP |
| `data/` | the sample pages — input only |
| `api/` | Vercel entrypoint wrapping `server.py` |
| `models.py` `main.py` `server.py` `ai.py` | see `.claude/rules/core-modules.md` |

`out/products.json` is a shipped artifact — the deployed API serves it, and it is the one path
under `out/` that is not gitignored. Only ever write it from a real keyed run.
