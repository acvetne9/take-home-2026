"""Tests for the extractor.

The first test is the important one. Site-specific or page-specific logic is an invariant of
this codebase, and that is not a thing you can verify by intending to comply — it is a thing
you verify by grepping your own source, including the comments, and failing the build. So it
runs over the AST *and* over the raw text of every module in `extract/`.

The rest are regression guards, not aspirational coverage. Each docstring says which failure
it is guarding against, because a test whose purpose is forgotten is a test that gets deleted
the first time it is inconvenient.
"""

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT))

from extract.dom import Document, decode, UndecodableDocument
from extract.harvest import harvest
from extract.graph import resolve_graph, product_nodes, walk
from extract.pipeline import extract_deterministic, _page_warnings, _confidence, _assemble
from extract import taxonomy as taxonomy_mod
from models import VALID_CATEGORIES

DATA = ROOT / "data"
PAGES = sorted(DATA.glob("*.html"))
SOURCE_DIRS = [ROOT / "extract", ROOT / "models.py", ROOT / "main.py", ROOT / "server.py"]

# Sites in the provided sample, plus the CDN and platform hostnames their markup uses.
# Any of these appearing in the extractor is a site rule by definition.
FORBIDDEN = [
    "nike", "llbean", "l.l.bean", "adaysmarch", "acehardware", "ace hardware",
    "article.com", "dewalt", "mozu", "centracdn", "cdni.", "static.nike",
]
# Words that legitimately contain a forbidden substring.
ALLOWED_CONTEXT = re.compile(r"\barticles?\b", re.I)


def _source_files():
    files = []
    for target in SOURCE_DIRS:
        if target.is_dir():
            files += sorted(target.glob("*.py"))
        elif target.exists():
            files.append(target)
    return files


@pytest.mark.parametrize("path", _source_files(), ids=lambda p: p.name)
def test_no_site_specific_logic(path):
    """THE disqualifier. No site name anywhere in the extractor, comments included.

    Comments count because a comment naming a site is evidence that the rule beneath it was
    written for that site, even when the rule itself looks generic.
    """
    text = path.read_text(encoding="utf-8")
    lowered = ALLOWED_CONTEXT.sub(" ", text).lower()
    hits = [needle for needle in FORBIDDEN if needle in lowered]
    assert not hits, f"{path.name} mentions {hits}"

    # A domain comparison is the other shape the rule takes: `if host == "..."`.
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            rendered = ast.dump(node).lower()
            if any(w in rendered for w in ("domain", "hostname", "netloc")) and \
                    any(isinstance(c, ast.Constant) and isinstance(c.value, str) for c in node.comparators):
                pytest.fail(f"{path.name}:{node.lineno} compares a domain against a literal")


def test_no_site_names_in_prompts():
    """Page-specific hints in prompts are disqualifying too, and prompts live in strings."""
    for path in (ROOT / "extract" / "reconcile.py", ROOT / "extract" / "taxonomy.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 80:
                lowered = ALLOWED_CONTEXT.sub(" ", node.value).lower()
                assert not [n for n in FORBIDDEN if n in lowered], \
                    f"{path.name}:{node.lineno} prompt names a site"


def test_secrets_are_not_committed():
    """The API key file must never end up in the repository.

    Matched per path rather than as a substring of the whole listing: `.env.example` *is*
    committed on purpose, and a substring test reads it as a leaked `.env`.
    """
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout
    tracked = [line for line in out.splitlines() if line]
    if (ROOT / ".git").exists():
        assert tracked, "git listed nothing: this guard would pass vacuously"
    for path in tracked:
        assert path != ".env", "the key file is tracked by git"


# -- decoding ---------------------------------------------------------------------------

def test_decode_honours_declared_charset():
    """Guards the `errors='replace'` habit, which turns an encoding bug into silent mojibake."""
    raw = "<meta charset='iso-8859-1'><h1>café</h1>".encode("iso-8859-1")
    assert "café" in decode(raw)


def test_decode_fails_loudly():
    """A page we cannot decode must raise, not produce replacement characters."""
    with pytest.raises(UndecodableDocument):
        decode(b"<meta charset='utf-8'>\xff\xfe\xff\xfe invalid")


# -- harvesting -------------------------------------------------------------------------

@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.stem)
def test_case_insensitive_harvest(path):
    """Regression guard: matching attributes case-sensitively found zero annotations on a page
    that ships JSX-cased spellings, and the failure was invisible because other carriers
    covered for it."""
    doc = Document.from_path(path)
    lowered = Document.from_text(doc.html.lower(), source=doc.source)
    fields = {c for c, _b in harvest(doc)}
    fields_lower = {c for c, _b in harvest(lowered)}
    missing = {c for c in fields if c.startswith(("microdata", "rdfa"))} - fields_lower
    assert not missing, f"markup carriers only found in one casing: {missing}"


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.stem)
def test_deref_terminates(path):
    """Normalised caches are cyclic graphs; the naive resolver raised RecursionError."""
    graph = resolve_graph(harvest(Document.from_path(path)))
    assert sum(1 for blob in graph for _p, _n in walk(blob)) > 0


# -- fields -----------------------------------------------------------------------------

EXPECTED = {
    # Our own reading of each page, not ground truth handed to us. Image counts were checked
    # against the page's own declared ordering where it states one.
    "ace":        {"price": 129.00, "currency": "USD", "images": 8,  "axes": 0, "video": False},
    "adaysmarch": {"price": 170.00, "currency": "USD", "images": 5,  "axes": 1, "video": True},
    "article":    {"price": 349.00, "currency": "USD", "images": 12, "axes": 0, "video": False},
    "llbean":     {"price": 29.95,  "currency": "USD", "images": 25, "axes": 3, "video": False},
    "nike":       {"price": 76.99,  "currency": "GBP", "images": 8,  "axes": 2, "video": False},
}


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.stem)
def test_price_and_currency(path):
    raw = extract_deterministic(Document.from_path(path))
    want = EXPECTED[path.stem]
    assert raw.fields.price == want["price"]
    assert raw.fields.currency == want["currency"]


def test_price_survives_without_framework_state():
    """One page states its price only in a markup annotation. Prove the annotation path works
    on its own, so it cannot be silently masked by the JSON path continuing to work."""
    from extract.markup import microdata
    from extract import fields as field_mod
    doc = Document.from_path(DATA / "llbean.html")
    graph = resolve_graph(microdata(doc))
    nodes = [n for n in product_nodes(graph) if n[0] >= 4]
    price, currency, _compare, source = field_mod.resolve_price_from_graph(nodes)
    assert price == 29.95 and currency == "USD", source


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.stem)
def test_images(path):
    raw = extract_deterministic(Document.from_path(path))
    assert len(raw.image_urls) == EXPECTED[path.stem]["images"]
    assert len(set(raw.image_urls)) == len(raw.image_urls), "duplicate image URLs"
    for url in raw.image_urls:
        assert url.startswith("http"), url
        # A data: URI is an inline placeholder, never a full-resolution asset.
        assert not url.startswith("data:")


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.stem)
def test_no_low_resolution_images(path):
    """B6 asks for full resolution; a correct count of thumbnails would still be wrong."""
    low = re.compile(r"-(?:thumb|mini|small|square|standard)\.|(?:Small|Medium)\d*(?:Retina)?\."
                     r"|[?&](?:w|wid)=\d{1,3}\b")
    raw = extract_deterministic(Document.from_path(path))
    assert not [u for u in raw.image_urls if low.search(u)]


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.stem)
def test_video(path):
    raw = extract_deterministic(Document.from_path(path))
    assert bool(raw.video_url) == EXPECTED[path.stem]["video"]


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.stem)
def test_variants(path):
    """Every product emits at least one variant row, so consumers see one shape."""
    raw = extract_deterministic(Document.from_path(path))
    assert len(raw.options) == EXPECTED[path.stem]["axes"]
    assert len(raw.variants) >= 1
    for variant in raw.variants:
        for axis in variant["options"]:
            assert any(o["name"] == axis for o in raw.options), f"{axis} not declared as an axis"


def test_variants_are_not_fabricated():
    """The axis cross-product is not the variant list. Where a page declares three axes we
    publish only the combinations its own SKU sets support, never every permutation."""
    raw = extract_deterministic(Document.from_path(DATA / "llbean.html"))
    permutations = 1
    for option in raw.options:
        permutations *= len(option["values"])
    assert 1 < len(raw.variants) < permutations


# -- category ---------------------------------------------------------------------------

def test_lexical_category_always_validates():
    """The property that matters more than accuracy: a wrong-but-valid leaf is a review item,
    an invalid one loses the whole product to a validation error."""
    for path in PAGES:
        raw = extract_deterministic(Document.from_path(path))
        query = taxonomy_mod.retrieval_query(
            raw.fields.name, raw.fields.brand, raw.fields.breadcrumb, raw.fields.description)
        assert taxonomy_mod.lexical_only(query) in VALID_CATEGORIES


def test_coerce_rescues_a_paraphrased_path():
    """Models re-word and re-space even when told to copy. Every one of those is recoverable."""
    tax = taxonomy_mod.load()
    shortlist = ["Home & Garden > Lighting > Lamps", "Home & Garden > Lighting"]
    for answer in ["Home & Garden>Lighting>Lamps", "home & garden > lighting > lamps",
                   "Lamps", "", "Home & Garden > Lighting > Lamps "]:
        assert tax.coerce(answer, shortlist) in VALID_CATEGORIES


def test_branch_expansion_reaches_a_lexical_miss():
    """The vocabulary-gap fix. One page's product type has no shared token with its taxonomy
    leaf, so it is unreachable by ranking alone and only reachable as a branch sibling."""
    tax = taxonomy_mod.load()
    raw = extract_deterministic(Document.from_path(DATA / "adaysmarch.html"))
    query = taxonomy_mod.retrieval_query(
        raw.fields.name, raw.fields.brand, raw.fields.breadcrumb, raw.fields.description)
    truth = "Apparel & Accessories > Clothing > Pants"
    shortlist = tax.shortlist(query, ["Apparel & Accessories", "Sporting Goods"])
    assert truth not in shortlist, "premise no longer holds; this test is measuring nothing"
    assert truth in tax.expand(shortlist)


# -- the confidence floor ---------------------------------------------------------------

_SHELL = """<html><head><title>Loading… | Example Store</title></head>
<body><div id="root" class="skeleton is-loading" aria-busy="true"></div></body></html>"""


def test_refuses_an_unrendered_shell():
    """`<title>` is a real non-empty string, so every "is it populated" check passes on a page
    that rendered nothing. Silently wrong is worse than empty at graph scale."""
    doc = Document.from_text(_SHELL, source="shell.html")
    raw = extract_deterministic(doc)
    warnings = _page_warnings(raw)
    assert _confidence(raw, warnings) < 0.35
    assert any("may not have rendered" in w for w in warnings)


def test_full_products_validate():
    """End to end, without a network: every page yields a schema-valid Product."""
    for path in PAGES:
        raw = extract_deterministic(Document.from_path(path))
        query = taxonomy_mod.retrieval_query(
            raw.fields.name, raw.fields.brand, raw.fields.breadcrumb, raw.fields.description)
        product = _assemble(raw, None, taxonomy_mod.lexical_only(query), 0)
        assert product.name and product.brand and product.description
        assert product.price.price > 0
        assert product.category.name in VALID_CATEGORIES
        assert product.image_urls


# -- repository hygiene ------------------------------------------------------------------


# ---------------------------------------------------------------------------------------
# Regressions.
#
# The price group matters most: every failure mode below publishes a *wrong number at high
# confidence*, which is the one thing a product graph cannot absorb.
# ---------------------------------------------------------------------------------------

FIXTURES = ROOT / "tests" / "fixtures"


def _fixture(name):
    return extract_deterministic(Document.from_path(FIXTURES / name))


def test_ambiguous_currency_is_not_guessed():
    """"kr" is four different currencies. Guessing one is the bug class we are removing."""
    from extract.fields import _SYMBOL_CURRENCY
    assert "KR" not in _SYMBOL_CURRENCY


def test_unpriceable_page_refuses_rather_than_publishing_zero():
    """`_assemble` coerced a missing price to 0.0 and a missing currency to "USD".

    A quote-only page cleared the confidence floor and published at $0.00 USD, which is
    precisely the row that canonicalises against a real product and corrupts its price.
    """
    from extract.pipeline import UnpriceablePage
    raw = _fixture("price_on_request.html")
    assert raw.fields.price is None
    with pytest.raises(UnpriceablePage):
        _assemble(raw, None, "Home & Garden > Decor", 30)


@pytest.mark.parametrize("fixture,carrier", [
    ("squarespace.html", "state:"),        # assignment to a non-window namespace
    ("sfcc.html", "attr:"),                # single-quoted attribute
    ("nextjs_rsc.html", "script-literal"),  # JSON inside a JS string literal (RSC flight data)
    ("nuxt_state.html", "state:"),         # IIFE-wrapped JS object literal
])
def test_carrier_is_readable(fixture, carrier):
    """Four production stacks yielded zero JSON blobs and refused at confidence 0.24."""
    blobs = harvest(Document.from_path(FIXTURES / fixture))
    assert any(c.startswith(carrier) for c, _ in blobs), \
        f"{fixture}: no {carrier} blob among {sorted({c for c, _ in blobs})}"


def test_js_literal_reader_drops_unresolvable_values():
    """The tolerant reader must never invent a value it cannot resolve.

    A bare identifier is a reference into a scope we are not evaluating. Dropping the key
    loses data; guessing at it fabricates data, which is strictly worse.
    """
    from extract.harvest import _loads_js
    assert _loads_js("{a:1,b:'two',c:[1,2,],}") == {"a": 1, "b": "two", "c": [1, 2]}
    assert _loads_js('{name:"A",brand:someVar,price:212}') == {"name": "A", "price": 212}
    assert _loads_js('{k:/*comment*/ 1, j: undefined}') == {"k": 1, "j": None}


# ---------------------------------------------------------------------------------------
# Regressions found on real pages.
#
# Third-party HTML is never committed, so the shapes are reproduced inline here as the
# smallest markup that triggers them. Both publish a wrong answer at high confidence.
# ---------------------------------------------------------------------------------------


def test_product_page_with_a_related_items_list_is_not_refused():
    """The false positive the two-signal rule exists to avoid: a real PDP whose markup also
    carries an ItemList. Every provided page must still be accepted."""
    for page in PAGES:
        raw = extract_deterministic(Document.from_path(page))
        assert not any("not a PDP" in w for w in _page_warnings(raw)), \
            f"{page.name} was wrongly flagged as a listing"


def test_provided_pages_are_not_caught_by_the_listing_guards():
    """Both guards above must leave every real PDP alone."""
    for page in PAGES:
        raw = extract_deterministic(Document.from_path(page))
        warnings = _page_warnings(raw)
        assert not any("not a PDP" in w or "may not have rendered" in w for w in warnings), \
            f"{page.name}: {warnings}"


_VARIANT_OUTRANKS_GROUP = """<!doctype html><html><head><title>Cape T-shirt</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"ProductGroup",
 "name":"Men's Cape T-shirt","brand":{"@type":"Brand","name":"Seaward"},
 "description":"An organic cotton tee with a relaxed fit and a chest pocket.",
 "image":["https://cdn.example.test/cape-tee-2000.jpg"],
 "hasVariant":[
  {"@type":"Product","name":"Men's Cape T-shirt XS / Brick Orange","sku":"CAPE-XS-BRK",
   "offers":{"@type":"Offer","price":"48.00","priceCurrency":"GBP"}},
  {"@type":"Product","name":"Men's Cape T-shirt S / Brick Orange","sku":"CAPE-S-BRK",
   "offers":{"@type":"Offer","price":"48.00","priceCurrency":"GBP"}}]}
</script></head><body><h1>Men's Cape T-shirt</h1><button>Add to bag</button>
<img src="https://cdn.example.test/cape-tee-2000.jpg"></body></html>"""


def test_a_variant_does_not_outrank_the_product_it_belongs_to(tmp_path):
    """The first `hasVariant` entry is a product-shaped node and scored above its own group,
    so the page published "Men's Cape T-shirt XS / Brick Orange" as the product name.

    Found on a long-tail storefront sampled from its own sitemap. Member nodes stay in the
    candidate list — on some pages they are the only product data there is — they just stop
    outranking the thing they are a member of.
    """
    p = tmp_path / "group.html"
    p.write_text(_VARIANT_OUTRANKS_GROUP, encoding="utf-8")
    raw = extract_deterministic(Document.from_path(p))
    assert raw.fields.name == "Men's Cape T-shirt", f"got the variant's name: {raw.fields.name!r}"
    assert raw.fields.price == 48.00


# ---------------------------------------------------------------------------------------
# Transient provider failures must not be mistaken for bad answers.
#
# The cascade escalates to a model that costs ~4x when an answer is defective. A 429 is not a
# defective answer, and treating it as one means a provider incident silently multiplies the
# bill at exactly the moment the provider is already struggling.
# ---------------------------------------------------------------------------------------

class _FakeRateLimit(Exception):
    status_code = 429
    class response:  # noqa: D106 - mimics the SDK's shape closely enough to be duck-typed
        status_code = 429
        headers = {"retry-after": "0"}


class _FakeBadRequest(Exception):
    status_code = 400


# -- the committed artefact ---------------------------------------------------------------
#
# `out/products.json` is the one build artefact the repository ships, because it is what the
# deployed API serves. Nothing else in this suite looks at it, so it is the one place where a
# wrong file can reach a reader — and it did: a `--no-ai` run writes the same path with
# lexically-guessed categories and no key features, which looks entirely plausible until you
# read a row. The signature is precise, so guard it precisely.
