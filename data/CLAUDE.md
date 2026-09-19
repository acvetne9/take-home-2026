# `data/`

Five product-detail pages, plus the `README.md` listing their source URLs. **Input only —
never edit these files, never add to them.**

Every sample-page number in the project README is measured against these exact bytes. One
changed character silently invalidates them.

## Build for N

**Never hardcode a page count** — anything assuming five is already a bug. The directory is
globbed, and the pipeline runs per document.

## They are a curriculum, not a sample

Each page exposes its data by a different mechanism, and each exercises one path the others
don't:

| the page's shape | what it forces |
|---|---|
| clean JSON-LD `Product`, price in the offer | the baseline every extractor handles |
| thin JSON-LD, real data in framework hydration state | state is often richer than the DOM |
| JSON-LD with **no offers**, price only in rendered DOM text | rendered text is a first-class price source, not a fallback |
| **no JSON-LD at all** — Microdata plus a large SSR blob | markup annotations are load-bearing, and their attribute names are camelCase |
| a `ProductGroup` with a large `hasVariant` array, no offers | variant rows are product-shaped and must not outrank their subject |

A JSON-LD-only extractor fails one page completely and loses price on two of five. That is the
argument for harvesting from eight carriers.

## What this corpus does and doesn't exercise

Two currencies, both Latin-script, both English; roughly 10 of 26 carrier conventions seen in
the wild; no mass-market commerce platform (Shopify, WooCommerce, Magento, BigCommerce,
Salesforce B2C). Four of five ship skeleton markup, and one page's price is a function of the
crawler's egress IP — locale belongs in the primary key, not in a parser.

Broader coverage lives in `tests/fixtures/`, which is written from published platform
documentation and exercises the carriers these five do not.
