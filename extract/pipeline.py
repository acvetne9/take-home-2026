"""Step 10 — orchestration, the confidence floor, and the escalation cascade.

Order matters here and is not arbitrary. Everything deterministic runs first, so that by the
time a model is involved the pipeline already knows the product's name, brand, price, images,
video and full variant matrix. The model is then asked only what is left — and, just as
importantly, the pipeline can decide whether to ask at all.

The confidence floor is the part that is easy to leave out and expensive to leave out. At 50
million products, a silently wrong row is worse than a missing one: a missing row is visible in
a dashboard, whereas a product named "Loading…" priced at $0 flows into the graph, gets
canonicalised against a real product, and corrupts it. So this module refuses:

  * a page that only rendered a shell, where the "name" would come from `<title>` alone;
  * a page with no price from any source *and* no structured data;
  * a document asserting many products with no buying affordance — a category listing reached
    by a redirect, which passes every "is this valid HTML" test there is.

The escalation cascade follows from the same reasoning. Most pages resolve cleanly on the
cheapest model; the ones that do not are identifiable *before* the answer is used — a missing
required field, a validation failure, an empty gallery. Those, and only those, are retried on a
stronger model, so the cost of the long tail is paid on the long tail.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass

from models import (Availability, Category, ExtractionResult, Price, Product,
                    Variant, VariantOption)

from . import bundle as bundle_mod
from . import fields as field_mod
from . import images as image_mod
from . import media as media_mod
from . import reconcile as reconcile_mod
from . import taxonomy as taxonomy_mod
from . import variants as variant_mod
from .dom import Document
from .graph import product_nodes, resolve_graph, walk
from .harvest import harvest

logger = logging.getLogger(__name__)


class UnpriceablePage(Exception):
    """Raised instead of publishing a product at $0.00. See `_assemble`."""

# Cheapest first. Escalation is by exception, not by default.
MODEL_CASCADE = ("openai/gpt-5-nano", "google/gemini-2.5-flash-lite", "openai/gpt-5-mini")

_MIN_NODE_SCORE = 4
_BUY_WORDS = re.compile(
    r"add to (?:cart|bag|basket)|buy now|add to trolley|pre-?order|out of stock|sold out"
    r"|in stock|checkout|InStock|OutOfStock", re.I)
_LISTING_THRESHOLD = 8
# Attempts per model before giving up on it and moving down the cascade, and the base delay
# between them. Small: at 50M products the queue is the place to absorb a real outage, not an
# in-process sleep. This exists so a *momentary* limit does not cost 4x the model bill.
_MODEL_ATTEMPTS = 3
_BACKOFF_BASE = 1.0
# Keys whose contents are *members of* the page's product rather than the product itself:
# schema.org's variant and recommendation collections. A node sitting under one of these is a
# legitimate product node — it is just not the one the page is about, so it must not win a
# field the subject node can supply. On one storefront the first variant outranked the group
# and the product was published as "Men's Cape T-shirt XS / Brick Orange".
_MEMBER_PATH = re.compile(
    r"hasVariant|itemListElement|isSimilarTo|isRelatedTo|isAccessoryOrSparePartFor|"
    r"related|recommend|similar|crossSell|upSell", re.I)
# Two *published* declarations, required together. Open Graph asks a page to state its own
# `og:type`, and a storefront sets `product` on a PDP; schema.org asks it to state its page
# type, and a category page is a `CollectionPage`. The older heuristic here — many product
# nodes and no buy words — misses a category page entirely, because a category page is full of
# "Add to cart" buttons, one per tile.
#
# Both signals are required because either alone has a real false positive: a PDP may embed a
# related-products `ItemList`, and plenty of PDPs set no `og:type` at all.
_PRODUCT_OG_TYPES = {"product", "product.item", "product.group", "og:product"}
_LISTING_PAGE_TYPES = re.compile(r'"@type"\s*:\s*"(?:CollectionPage|SearchResultsPage)"', re.I)


@dataclass
class _Raw:
    """Everything the deterministic stages produced, before any model is involved."""
    doc: Document
    graph: list
    nodes: list
    fields: field_mod.Fields
    image_urls: list[str]
    video_url: str | None
    options: list[dict]
    variants: list[dict]
    colors: list[str]


def _breadcrumb(doc: Document, graph) -> list[str]:
    """The merchant's own category path — annotated trail first, DOM landmark second."""
    from .markup import breadcrumb_from_dom, breadcrumb_from_node
    trail: list[str] = []
    for blob in graph:
        for _path, node in walk(blob):
            found = breadcrumb_from_node(node)
            if len(found) >= 2:
                trail = found
                break
        if trail:
            break
    trail = trail or breadcrumb_from_dom(doc)
    # Nearly every breadcrumb starts at the storefront itself. The site's name is the one crumb
    # guaranteed to carry no category signal, so drop it when the page tells us what it is.
    site = (doc.meta("site_name") or "").strip().lower()
    if trail and site:
        head = re.sub(r"[^a-z0-9]", "", trail[0].lower())
        if head and head == re.sub(r"[^a-z0-9]", "", site):
            trail = trail[1:]
    return trail


def extract_deterministic(doc: Document) -> _Raw:
    """Everything that can be read from the page without asking anyone anything."""
    graph = resolve_graph(harvest(doc))
    nodes = [n for n in product_nodes(graph) if n[0] >= _MIN_NODE_SCORE]
    # Subject nodes first, member nodes after — a stable partition, so the scorer's ordering
    # still decides within each group. Members stay in the list because on some pages they are
    # the only product data there is; they simply stop outranking the thing they belong to.
    nodes.sort(key=lambda n: bool(_MEMBER_PATH.search(n[1] or "")))
    all_dicts = [(p, n) for blob in graph for p, n in walk(blob) if isinstance(n, dict)]

    fields = field_mod.Fields()
    fields.name, name_src = field_mod.resolve_name(doc, nodes)
    fields.brand, brand_src = field_mod.resolve_brand(doc, nodes)
    fields.description, desc_src = field_mod.resolve_description(doc, nodes)
    fields.price, fields.currency, fields.compare_at_price, price_src = \
        field_mod.resolve_price(doc, nodes)
    fields.breadcrumb = _breadcrumb(doc, graph)
    fields.sources = {k: v for k, v in
                      {"name": name_src, "brand": brand_src, "description": desc_src,
                       "price": price_src}.items() if v}

    image_urls = image_mod.resolve(doc, graph)
    video_url = media_mod.resolve_video(doc, graph)
    url_tokens, key_tokens = image_mod.identity_tokens(doc, all_dicts)
    fallback_sku = next(
        (n.get("sku") for _s, _p, n in nodes if isinstance(n.get("sku"), str)), None)
    options, variants, colors = variant_mod.derive(
        graph, url_tokens + key_tokens, fields.name, fallback_sku)
    return _Raw(doc, graph, nodes, fields, image_urls, video_url, options, variants, colors)


def _page_warnings(raw: _Raw) -> list[str]:
    """Reasons to distrust this page, gathered before the answer is assembled."""
    warnings: list[str] = []
    doc = raw.doc

    if raw.fields.sources.get("name", "").startswith(("dom:title", "meta:")) and not raw.nodes:
        # Nothing but the document's own metadata, and no structured data behind it: a
        # client-rendered shell whose content has not arrived, or a themed error page. Both
        # `<title>` and `og:title` are real non-empty strings that every "is it populated"
        # check passes, which is exactly why this needs its own refusal.
        #
        # Gating on `dom:title` alone was too narrow: a storefront's soft-404 sets `og:title`
        # to "404 Not Found" and we published that as a product, priced from a cross-sell tile
        # that happened to be on the error page.
        warnings.append("name came from document metadata with no structured data: "
                        "page may not have rendered")
    if raw.fields.price is None:
        warnings.append("no price found in structured data, markup annotations or page text")
    if not raw.image_urls:
        warnings.append("no images resolved")
    if not raw.fields.description:
        warnings.append("no description found")

    # A listing page reached by redirect asserts many products and offers no way to buy any one
    # of them. Every other structural test passes on such a page.
    product_like = sum(
        1 for _s, _p, n in raw.nodes
        if str(n.get("@type", "")).lower().rstrip("/").split("/")[-1] in ("product", "productgroup")
    )
    body = doc.tree.css_first("body")
    has_buy = bool(_BUY_WORDS.search(body.text(separator=" ")[:200_000])) if body else False
    if not has_buy:
        has_buy = bool(_BUY_WORDS.search(doc.html[:400_000]))
    if product_like >= _LISTING_THRESHOLD and not has_buy:
        warnings.append(f"{product_like} product nodes and no buy affordance: looks like a listing page")

    og_type = (doc.meta("type") or "").strip().lower()
    if og_type and og_type not in _PRODUCT_OG_TYPES and _LISTING_PAGE_TYPES.search(doc.html):
        warnings.append(f"page declares og:type={og_type!r} and a listing page type: not a PDP")

    # The page's own markup can say outright that what we read is a list member. schema.org's
    # `itemListElement` means "one of many"; a PDP states its product as the page's subject,
    # not as element [0] of a list. This is the signal that catches a category page which
    # declares itself `og:type=product.group` — retailers do, for a range page — and is also
    # the honest reading of what happened: we resolved the right field off the wrong node.
    if any("itemListElement" in src for src in raw.fields.sources.values()):
        warnings.append("product fields resolved from an itemListElement: not a PDP")
    return warnings


def _confidence(raw: _Raw, warnings: list[str]) -> float:
    """A blunt, explainable score. Its purpose is triage, not calibration.

    Weighted by how load-bearing each field is for a product graph: a row without a price or a
    name is not a product, whereas a row without a video is just a row without a video.
    """
    score = 0.0
    score += 0.30 if raw.fields.name else 0.0
    score += 0.25 if raw.fields.price is not None else 0.0
    score += 0.15 if raw.image_urls else 0.0
    score += 0.10 if raw.fields.brand else 0.0
    score += 0.10 if raw.fields.description else 0.0
    score += 0.05 if raw.fields.breadcrumb else 0.0
    score += 0.05 if raw.nodes else 0.0
    # Structured data is the difference between reading a fact and inferring one.
    if any(s.startswith("json") for s in raw.fields.sources.values()):
        score = min(1.0, score + 0.05)
    for w in warnings:
        if "may not have rendered" in w or "listing page" in w:
            score *= 0.4
        else:
            score *= 0.85
    return round(min(score, 1.0), 3)


# A renamed axis has to be *better* than what the page said, not merely different.
_BAD_AXIS_NAME = re.compile(r"[/|,>]|\bvariant\b|\boption\b|\bitem\b|^\W*$", re.I)


def _axis_renames(result, options) -> dict[str, str]:
    """Accept the model's axis names only where they are unambiguous improvements.

    Renaming an axis is a judgement call the model is well suited to - a page whose axis is
    called "Item" and whose values are Regular and Tall is describing a Fit. But it is a
    judgement it can also get wrong, and a wrong axis label is worse than an awkward one: it
    misrepresents what the shopper is choosing, and a rename that collides with a *real* axis
    silently merges two different dimensions into one.

    So we reject any rename that is multi-word, carries a separator, or collides with another
    axis, and keep the page's own word instead. The page's word is never wrong, only plain.
    """
    original_names = {o["name"] for o in options}
    renames: dict[str, str] = {}
    for pair in (result.axis_names if result else []):
        source, target = (pair.original or "").strip(), (pair.normalized or "").strip()
        if not source or not target or source == target:
            continue
        if _BAD_AXIS_NAME.search(target) or len(target.split()) > 2 or len(target) > 24:
            continue
        # Would this rename collide with an axis that already exists, or with another rename?
        collides = target.lower() in {n.lower() for n in original_names if n != source}
        if collides or target.lower() in {v.lower() for v in renames.values()}:
            continue
        renames[source] = target
    return renames


def _assemble(raw: _Raw, result: reconcile_mod.Reconciliation | None,
              category_path: str, image_window: int) -> Product:
    renames = _axis_renames(result, raw.options)

    options = [
        VariantOption(name=renames.get(o["name"], o["name"]), values=o["values"])
        for o in raw.options if o["values"]
    ]
    variants = []
    for v in raw.variants:
        price = None
        if v.get("price") and v["price"].get("price") is not None:
            price = Price(price=v["price"]["price"],
                          currency=v["price"].get("currency") or raw.fields.currency or "USD",
                          compare_at_price=v["price"].get("compare_at_price"))
        variants.append(Variant(
            sku=v.get("sku"),
            options={renames.get(k, k): val for k, val in (v.get("options") or {}).items()},
            price=price,
            image_urls=v.get("image_urls") or [],
            availability=Availability(v.get("availability", "unknown")),
            gtin=v.get("gtin"),
            mpn=v.get("mpn"),
        ))

    image_urls = raw.image_urls
    if result:
        image_urls = reconcile_mod.apply_image_judgement(
            image_urls, result.product_image_indices, image_window)

    # Colours: whatever the page's own Color axis says, and the model's reading only when the
    # page states no axis at all. The axis is data; the model's answer is a judgement about
    # prose, and data wins whenever we have it.
    colors = raw.colors or [c for c in (result.colors if result else []) if c]

    if raw.fields.price is None:
        # Never coerce a missing price to 0.0. `Product.price` is required, so a page we could
        # not price has no valid Product — and a $0.00 row is the single most corrosive thing
        # this pipeline could emit, because it canonicalises against a real product and drags
        # its price down. Callers must refuse; this makes that non-optional.
        raise UnpriceablePage(f"no price could be read from {raw.doc.source}")

    return Product(
        name=raw.fields.name or "",
        price=Price(price=raw.fields.price,
                    currency=raw.fields.currency or "USD",
                    compare_at_price=raw.fields.compare_at_price),
        description=raw.fields.description or "",
        key_features=[f for f in (result.key_features if result else []) if f][:6],
        image_urls=image_urls,
        video_url=raw.video_url,
        category=Category(name=category_path),
        brand=raw.fields.brand or "",
        colors=colors,
        variants=variants,
        options=options,
    )


async def extract(doc: Document, ai_module, model: str | None = None,
                  min_confidence: float = 0.35, allow_cascade: bool = True) -> ExtractionResult:
    """HTML in, a validated `Product` (or an honest refusal) out."""
    raw = extract_deterministic(doc)
    warnings = _page_warnings(raw)
    confidence = _confidence(raw, warnings)

    # A page with no price is refused outright, whatever its confidence. Quote-only (MAP)
    # pages, login-gated B2B pricing and unparseable localized money all land here, and all
    # three are pages where the honest output is nothing rather than zero.
    # Both of these are categorical, not matters of degree, so they refuse outright rather than
    # leaning on the confidence floor. A rendered bot-wall homepage once scored 0.36 against a
    # 0.35 floor and published its own strapline as a product at EUR 1.00 — the guard had fired
    # and the arithmetic still let it through, which is the wrong way for this to be decided.
    if any("not a PDP" in w or "may not have rendered" in w for w in warnings):
        logger.warning("refusing %s: %s", doc.source, warnings[0])
        return ExtractionResult(product=None, url=doc.canonical_url(), source=doc.source,
                                confidence=confidence, warnings=warnings,
                                field_sources=raw.fields.sources)

    if raw.fields.price is None:
        logger.warning("refusing %s: no price could be read", doc.source)
        return ExtractionResult(product=None, url=doc.canonical_url(), source=doc.source,
                                confidence=confidence, warnings=warnings,
                                field_sources=raw.fields.sources)

    if confidence < min_confidence:
        # Refuse rather than publish. This is a decision about the *page*, which is why no
        # amount of extra parsing fixes it: there is nothing on the page to parse.
        logger.warning("refusing %s: confidence %.2f (%s)", doc.source, confidence,
                       "; ".join(warnings))
        return ExtractionResult(product=None, url=doc.canonical_url(), source=doc.source,
                                confidence=confidence, warnings=warnings,
                                field_sources=raw.fields.sources)

    query = taxonomy_mod.retrieval_query(
        raw.fields.name, raw.fields.brand, raw.fields.breadcrumb, raw.fields.description)
    evidence = bundle_mod.build(doc, raw.graph, raw.nodes, raw.fields, raw.image_urls, raw.options)

    models = [model] if model else list(MODEL_CASCADE)
    if not allow_cascade:
        models = models[:1]

    result, category_path, used = None, None, None
    tax = taxonomy_mod.load()

    async def _ask(candidate_model):
        """One model's two calls, retrying *this* model through transient provider failures."""
        for attempt in range(1, _MODEL_ATTEMPTS + 1):
            try:
                roots, phrase = await taxonomy_mod.classify_roots(
                    query, candidate_model, ai_module)
                # The expansion phrase is weighted by repetition rather than by a coefficient:
                # the ranker is TF-IDF, so repeating the phrase is how you say "this matters
                # more".
                expanded = f"{phrase} {phrase} {query}" if phrase else query
                shortlist = tax.expand(tax.shortlist(expanded, roots) or tax.shortlist(expanded))
                return await reconcile_mod.reconcile(
                    evidence, shortlist, candidate_model, ai_module), shortlist
            except Exception as exc:
                if not reconcile_mod.is_transient(exc) or attempt == _MODEL_ATTEMPTS:
                    raise
                delay = reconcile_mod.retry_after(exc)
                if delay is None:
                    delay = _BACKOFF_BASE * (2 ** (attempt - 1))
                # Jitter, because 50M products retrying on the same schedule is a thundering
                # herd aimed at a provider that has already told us it is struggling.
                delay += random.uniform(0, 0.5)
                logger.info("%s transient on %s (%s); retry %d/%d in %.1fs",
                            candidate_model, doc.source, type(exc).__name__,
                            attempt, _MODEL_ATTEMPTS, delay)
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable")

    for candidate_model in models:
        try:
            result, shortlist = await _ask(candidate_model)
            if result is None:
                continue
            category_path, how = tax.coerce_with_reason(result.category_path, shortlist)
            used = candidate_model
            # Escalate only on a concrete defect, never on a hunch. A category we had to *guess*
            # at means the model stopped copying from the list; an empty feature list on a page
            # with plenty of text means it stopped reading. Formatting differences are not
            # defects — an answer that only needed re-spacing is a correct answer.
            defective = how in ("guessed", "fallback") or \
                        (not result.key_features and len(evidence) > 4000) or \
                        (not result.product_image_indices and len(raw.image_urls) > 1)
            if not defective:
                break
            logger.info("escalating %s from %s (%s)", doc.source, candidate_model,
                        "category " + how if how in ("guessed", "fallback") else "thin answer")
        except Exception as exc:
            logger.warning("model %s failed on %s: %s", candidate_model, doc.source, exc)
            continue

    if category_path is None:
        # Every model failed. The lexical retriever still gives a valid, if blunter, answer —
        # losing the product entirely over a category would be a worse trade.
        category_path = taxonomy_mod.lexical_only(query)
        warnings.append("category resolved lexically: no model response")

    product = _assemble(raw, result, category_path, bundle_mod._IMAGE_LIST)
    if used and used != models[0]:
        warnings.append(f"escalated to {used}")
    return ExtractionResult(product=product, url=doc.canonical_url(), source=doc.source,
                            confidence=confidence, warnings=warnings,
                            field_sources=raw.fields.sources)
