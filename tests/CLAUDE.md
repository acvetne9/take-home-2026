# `tests/`

Regression guards, not coverage. **Each docstring names the failure it guards** — keep that
convention, because a test whose purpose is forgotten is a test that gets deleted the first
time it is inconvenient.

`pytest` runs the suite with **no network**. Keep it that way.

## The two guard tests

`test_no_site_specific_logic` and `test_no_site_names_in_prompts` enforce the repo's
invariants over the AST and raw text of every module in `extract/`. If one fails, rewrite the
rule against a published convention — never relax the test.

## Check who wrote the inputs

**A test is evidence only to the extent its inputs came from somewhere other than the belief
being tested.** Hand-authored inputs bound the failures from below, never above.

Diagnostic: **a stable mechanism with unstable failures is measuring its input.** If the code
has not moved and the set of failing cases keeps changing identity between runs, the inputs are
the problem.

## `tests/fixtures/`

Fifteen synthetic PDPs written from **published platform documentation** — WooCommerce,
Magento, Shopify, Microdata-only, a client-rendered shell, lazy-loaded media, App Router RSC,
Nuxt, Salesforce B2C, Squarespace, a comma-decimal locale, a four-seller marketplace, a
quote-only page, unit pricing, and a Shift-JIS page. They make "cite a published convention"
executable.

- They share an author with the code, so **a pass proves nothing; a failure proves a gap.**
- **They measure carrier *access*, not precision.** 0.4–3.7 KB with 3–5 image URLs, against
  real pages at 313–777 KB with 179–340.
- When adding one, copy spellings and structure **out of the platform's own published output**,
  never from memory.

## Real pages

Third-party HTML is **never committed** — it would spray brand names through the tree. So a
regression found on a real page is pinned as the **smallest inline markup string that
reproduces its shape**, not as the page. Write new guards that way.

## Numbers

Sample-page expectations are pinned here and reproduce from a clean clone. If you change a
number in `README.md`, say where it was measured.
