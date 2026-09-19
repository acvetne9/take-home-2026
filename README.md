# Product extraction

Live: https://product-pages-demo.vercel.app

Raw product-detail HTML in, validated `Product` records out, plus a small catalog UI over the
result.

Five pages, five products, no site-specific code:

| page | price | images | video | variants | axes | category |
|---|---|---|---|---|---|---|
| hardware retailer | $129.00 | 8 | — | 1 | — | Hardware > Tools > Drills > Handheld Power Drills |
| menswear brand | $170.00 | 5 | ✓ | 6 | Size | Apparel & Accessories > Clothing > Pants |
| furniture brand | $349.00 | 12 | — | 1 | — | Home & Garden > Lighting > Lamps |
| outdoor retailer | $29.95 | 25 | — | 83 | Color, Item, Size | Apparel & Accessories > Clothing > Shirts & Tops |
| sportswear brand | £76.99 (from £109.99) | 8 | — | 17 | Color, Size | Apparel & Accessories > Shoes |


---

## Running it

```bash
uv sync                       # or: pip install -e '.[dev]'
source .venv/bin/activate     # required: `uv sync` creates the venv but does not activate it
cp .env.example .env          # add OPEN_ROUTER_API_KEY

python main.py                # extract ./data/*.html -> ./out/products.json
python main.py --no-ai        # deterministic only -> ./out/products.no-ai.json
pytest                        # 72 tests, no network needed
```

Then the UI, in two terminals:

```bash
python server.py              # FastAPI on :8000  (docs at /docs)
cd web && npm install && npm run dev      # Vite on :5173, proxies /api to :8000
```

`main.py` also takes `--model` to pin a single model, `--no-cascade` to disable escalation, and
explicit paths to run one page.

---

## How extraction works

The pipeline is deterministic first and a model call last:

```
dom → markup → harvest → graph → fields ─┬─ images ─┬─ bundle → taxonomy + reconcile → pipeline
                                          ├─ media   │
                                          └─ variants┘
```

A product page is a document already rendered from a database, and most of that database is
still in the file — in JSON-LD, Microdata, RDFa, Open Graph, framework hydration state and the
rendered DOM itself. Eight carriers are harvested, references are resolved into one graph, and
the product node is selected by scoring. By the time a model is involved, name, brand, price,
currency, images, video and the full variant matrix are already known, so the pipeline decides
**whether to ask at all**.

The one structured call answers only what nobody stated: a category line copied from a
retrieved shortlist of the 5,595-leaf taxonomy, feature bullets, colour names, axis renames and
image *indices* — never a URL, a price or a variant row. Those are derived in Python, where
they can be checked. The page is reduced to an ordered evidence bundle before the call, and
output stays under 200 tokens.

Missing values stay missing. `UnpriceablePage` and the other refusal guards are categorical —
a page with no price it can stand behind produces no row rather than a `$0.00` one.

## Variant schema

Variants are published as two shapes. `options` answers *what can I choose?* (the axes);
`variants` answers *what happens if I choose?* (concrete SKUs with their own price,
availability, `gtin` and `mpn`). A flat list loses the combination and the per-SKU price; SKUs
alone force every consumer to re-derive the axes. `Variant.options` is `dict[str, str]`, so an
unseen axis — voltage, scent, inseam — needs no code change.

## Frontend

Vite + React + TypeScript + Tailwind v4, shadcn-idiom primitives kept in-tree. Two pages:

- **`/` catalog** — responsive grid, locked aspect ratios so nothing reflows as images land, lazy
  loading, skeletons, brand and category filters, struck-through compare-at prices, colour
  swatches that resolve real colour names and stay silent rather than guessing when they cannot.
- **`/product/:id` PDP** — full-resolution gallery with a thumbnail rail, arrow-key navigation and
  inline video; a variant picker driven by `options`, where choosing a colour greys out the sizes
  that colour is not made in and selecting a full combination swaps in that variant's own price,
  availability, SKU, GTIN and photographs.

The picker is where the schema decision becomes visible: greying out unavailable combinations is
only possible because the axes and the concrete SKUs are published separately.

Card → PDP uses the View Transitions API so the card's photograph grows into the gallery hero,
guarded by `prefers-reduced-motion`. Dark mode, focus rings, alt text, empty/error/404 states, and
prices formatted with `Intl.NumberFormat` in the currency the page actually stated — one sample
page prices in GBP because the crawl reached it from a UK edge, and rendering that as dollars
would be a quietly wrong number.

## API

Extraction and serving are decoupled. `main.py` writes `out/products.json`; `server.py` loads
what ingestion wrote and serves it read-only, with filtering by exact facet rather than free
text, because the facets are the part extracted deterministically. At scale the JSON file is
the only piece that changes — a real store goes in the middle, and the two halves stay
independent.


## System Design

While completing this project, I noticed something from the sample documents that I kept with me while completing this project: a product page isn't something to comprehend, it's a doc rendered from a database and you can decipher.You can cheaply rebuild variants of the database without extra token usage by only ever looking at one document, so there is nothing shared between products and going from 5 to 50 million is a queue and more workers. I measured the naive approach at ~$496,374 per 50M pages against about $12K with mine. At that size I'd spend a model once per site to learn how to read it, then reuse that for the site's whole catalog. 

The part that doesn't scale well is staleness. When making this project, I assumed that that when fetching a price, it is truth and at scale I would want to re-design it to be more flexible: structural things about a product (size, colors) often stay the same but re-read prices more often. Also, price/availability depends on market and people in NY versus the UK and is never tracked. At scale, I would want to also know who can access what markets so I know if it is available to them.

For the frontend api, I would expose the catalog as an endpoint that returns structured products (what is it/price). I also want to answer the question of "where to buy this and how" so I would have an endpoint that takes inputs like "yellow, medium" and turns them into a way to add to a cart. Beyond the API, I'd give developers webhooks for price and stock changes so nobody has to re-check fifty million products to catch a sale, and a sandbox running against a frozen copy of the catalog, since you can't test a shopping flow against prices that keep moving.