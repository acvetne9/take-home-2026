---
paths:
  - "models.py"
  - "main.py"
  - "server.py"
  - "ai.py"
---

# Root-level modules

These four have no directory of their own, so their conventions load from here when you open
one of them.

## `models.py` — the schema is the contract

- **`Product.category` has a validator that rejects anything outside the taxonomy file.** That
  makes a *generated* category a hard failure rather than a quality problem, which is why
  `extract/taxonomy.py` copies paths instead of producing them.
- **Variants are published as two shapes, deliberately.** `options` answers *what can I
  choose?* (axes); `variants` answers *what happens if I choose?* (concrete SKUs with price,
  availability, `gtin`, `mpn`). A flat list loses the combination and per-SKU price; SKUs alone
  force every consumer to re-derive the axes.
- **`Variant.options` is `dict[str, str]`, not an enum**, so unseen axes — voltage, scent,
  inseam — need no code change. Fixing a vocabulary of option names is quiet over-fitting.
- **`Product` is never used as a model response schema.** Open-ended maps are rejected by
  structured outputs; this is only safe because variants are derived in Python.
- **Carry `gtin` and `mpn` wherever the page states them.** They are the blocking keys for
  entity resolution and the most valuable thing this extractor hands a product graph.
- New pydantic models go here rather than beside their use.

## `ai.py` — vendored, do not modify

- It is a fixed dependency: the model client wrapper, kept as supplied so it stays swappable.
- Consequences elsewhere: `extract/reconcile.py` **duck-types provider exceptions** on HTTP
  status then class name rather than importing the SDK.
- **`_log_usage` adds `reasoning_tokens` on top of `output_tokens`, which already include
  them.** Never derive a cost or a savings ratio straight from its output.

## `main.py` — ingestion

- Writes `out/products.json`, which the deployed API serves.
- **`--no-ai` takes the lexical-only taxonomy path**: no model call, so no categories worth
  trusting, no `key_features`, and a fixed confidence. It writes to
  `out/products.no-ai.json`. Never let a `--no-ai` or missing-key run land on the default
  output path.
- Accepts explicit paths, `--model`, `--no-cascade` for single-page debugging.

## `server.py` — read-only

- Loads what ingestion wrote and serves it. **Extraction and serving stay decoupled** — the same
  split you would want at scale, with a real store in the middle instead of a JSON file.
- Filtering is by exact facet, not free text, because the facets are what was extracted
  deterministically and can be stood behind.
- No write paths, no extraction, no network.
