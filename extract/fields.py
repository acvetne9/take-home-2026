"""Step 4 — resolve the scalar fields, one field at a time.

The tempting design is to find "the product node" and read every field off it. Four successive
versions of that failed, for a reason that is structural rather than fixable: pages do not put
all the facts in one object. One sampled page states its name in a schema.org annotation, its
price in a hydration cache, and its images in markup. A single-node selector is therefore
forced to be wrong about something.

So each field gets its own priority chain, running over *all* product-shaped nodes in
descending order of how product-shaped they are, then falling back to Open Graph, then to the
DOM. Different fields legitimately resolve from different carriers on the same page.

Two traps are handled explicitly because both are invisible on a page that happens to work:

* **A placeholder is not null.** "First non-null wins" assumes the alternative to a real value
  is an absent one. On a modern page the alternative is a *skeleton*: real markup, earlier in
  the document than the content it stands in for, containing plausible-looking filler. Most of
  the sample pages ship skeleton markup; they pass by luck, and on a streamed page the real
  payload arrives *after* the shell, so the luck runs out. We reject values whose DOM context
  says placeholder, and reject sentinel strings.

* **Money needs product scoping.** A PDP contains the product's price, a cross-sell's price, a
  free-shipping threshold and a rewards amount. Taking the first money-shaped string finds one
  of the others about as often as not. We scope to the DOM subtree around the product title,
  exclude merchandising vocabulary, and only then read money.
"""

from __future__ import annotations

import html as html_mod
import re
from dataclasses import dataclass, field as dc_field

from .dom import Document, ancestors, context_words
from .graph import walk

# -- placeholder / sentinel rejection ---------------------------------------------------

# Generic loading-state vocabulary. Every one of these words is framework-neutral; we are not
# matching a site's class name, we are matching the industry's word for "not content yet".
_PLACEHOLDER_CONTEXT = re.compile(
    r"skeleton|shimmer|placeholder|is-?loading|loading-?state|aria-busy:\s*true|lqip|blurhash",
    re.I,
)
_SENTINEL = re.compile(
    r"^\s*(?:loading|please wait|n/?a|tbd|undefined|null|none|--+|—|\.{2,}|…| )*\s*$",
    re.I,
)
_TITLE_SUFFIX = re.compile(r"\s*[|–—·•]\s*[^|–—·•]{1,60}$")


def is_sentinel(value) -> bool:
    """A string that is markup for 'no value yet' rather than a value."""
    if not isinstance(value, str):
        return False
    return bool(_SENTINEL.match(value)) or value.strip().lower() in {"loading...", "loading…"}


def in_placeholder(node) -> bool:
    return bool(_PLACEHOLDER_CONTEXT.search(context_words(node, limit=6)))


def clean_text(value) -> str | None:
    """Normalise a harvested string, or reject it."""
    if isinstance(value, (int, float)):
        return str(value)
    if not isinstance(value, str):
        return None
    text = re.sub(r"<[^>]+>", " ", value)
    # Entities survive JSON decoding, because the encoder escaped them before serialising:
    # a product name arrives as "Kit (Battery &amp; Charger)" and would reach the UI that way.
    if "&" in text and re.search(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]{2,8});", text):
        text = html_mod.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text or is_sentinel(text):
        return None
    return text


# -- scalar fields ----------------------------------------------------------------------

_NAME_KEYS = ("name", "title", "productName", "product_name", "displayName")
_DESC_KEYS = ("description", "productDescription", "longDescription", "details", "detail")
_BRAND_KEYS = ("brand", "manufacturer", "vendor", "brandName", "brand_name")


@dataclass
class Fields:
    name: str | None = None
    brand: str | None = None
    description: str | None = None
    price: float | None = None
    currency: str | None = None
    compare_at_price: float | None = None
    breadcrumb: list[str] = dc_field(default_factory=list)
    sources: dict[str, str] = dc_field(default_factory=dict)


def _first_from_nodes(nodes, keys, transform=clean_text, min_len: int = 1):
    """Walk product-shaped nodes in order; return the first usable value for any of `keys`."""
    for _score, path, node in nodes:
        for key in keys:
            if key not in node:
                continue
            value = transform(node[key])
            if value and len(value) >= min_len:
                return value, f"json:{path}.{key}" if path else f"json:{key}"
    return None, None


def _brand_value(raw):
    """`brand` is a string on some pages and a `{"@type":"Brand","name":...}` on others."""
    if isinstance(raw, dict):
        return clean_text(raw.get("name") or raw.get("brand") or raw.get("title"))
    if isinstance(raw, list) and raw:
        return _brand_value(raw[0])
    return clean_text(raw)


def resolve_name(doc: Document, nodes) -> tuple[str | None, str | None]:
    value, src = _first_from_nodes(nodes, _NAME_KEYS, min_len=2)
    if value:
        return value, src
    og = clean_text(doc.meta("title"))
    if og:
        return og, "meta:og:title"
    # `<title>` last, and it is the weakest source there is: it carries site branding, and on a
    # page that has not rendered it is the *only* thing present, which is how "Loading…" becomes
    # a product name. The pipeline's confidence floor refuses a product resolved this way alone.
    title = clean_text(doc.title())
    if title:
        return _TITLE_SUFFIX.sub("", title).strip() or title, "dom:title"
    return None, None


def resolve_brand(doc: Document, nodes) -> tuple[str | None, str | None]:
    for _score, path, node in nodes:
        for key in _BRAND_KEYS:
            if key in node:
                value = _brand_value(node[key])
                if value and len(value) < 80:
                    return value, f"json:{path}.{key}" if path else f"json:{key}"
    og = clean_text(doc.meta("site_name"))
    if og:
        return og, "meta:og:site_name"
    return None, None


def resolve_description(doc: Document, nodes) -> tuple[str | None, str | None]:
    value, src = _first_from_nodes(nodes, _DESC_KEYS, min_len=24)
    if value:
        return value, src
    og = clean_text(doc.meta("description"))
    if og:
        return og, "meta:og:description"
    return None, None


# -- price --------------------------------------------------------------------------------

_PRICE_KEYS = ("price", "lowPrice", "amount", "finalPrice", "salePrice", "currentPrice",
               "value", "priceAmount", "unitPrice")
# Matched as a pattern rather than a fixed list: every platform spells the was-price
# differently (`listPrice`, `catalogListPrice`, `compareAtPrice`, `regularPrice`, `msrp`),
# and enumerating the spellings we happen to have seen is how an extractor stops generalising.
_COMPARE_KEY = re.compile(
    r"(?:list|compare|compare_?at|regular|was|original|strike|strikethrough|full|retail|high)_?price"
    r"|^msrp$|^price_?before", re.I)
_CURRENCY_KEYS = ("priceCurrency", "currency", "currencyCode", "priceCurrencyCode")

_SYMBOL_CURRENCY = {
    "$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY", "₹": "INR", "₽": "RUB", "₺": "TRY",
    "₩": "KRW", "C$": "CAD", "A$": "AUD", "R$": "BRL", "NZ$": "NZD", "S$": "SGD",
    "HK$": "HKD", "US$": "USD", "SFR": "CHF", "ZŁ": "PLN",
    # Fullwidth forms. CJK pages use these, not the ASCII/Latin-1 codepoints.
    "￥": "JPY", "＄": "USD", "￡": "GBP", "￦": "KRW",
}
# Deliberately absent: "kr". It is Swedish, Norwegian, Danish and Icelandic at once, and
# guessing one is the same class of silent error this module exists to remove.
# A currency token: a symbol (optionally prefixed by a country letter, as in C$/R$/US$), or an
# ISO 4217 code. Kept as one fragment so it can be used on either side of the number.
_CUR = (r"(?:(?:C|A|R|US|NZ|S|HK)?[$]|[£€¥₹₽₺₩\uffe5\uff04\uffe1\uffe6]|SFr\.?|z\u0142"
        r"|\b(?:USD|EUR|GBP|CAD|AUD|JPY|CHF|SEK|NOK|DKK|PLN|CZK|HUF|RON|BRL|MXN|ARS|CLP"
        r"|INR|CNY|KRW|TRY|ZAR|NZD|SGD|HKD|AED|SAR|ILS|THB|PHP|IDR|MYR|VND|RUB)\b)")
# A number that may be grouped with "." "," a space or a non-breaking space — every grouping
# convention in CLDR. Which separator is decimal is decided in `_as_amount`, not here.
_NUM = r"[0-9][0-9.,\u00a0\u202f\u2009 ]{0,15}[0-9]|[0-9]"
# Both orders. The Eurozone, Scandinavia and most of Latin America put the symbol AFTER the
# amount ("1.234,56 €"); English-speaking markets put it before. Neither is the general case.
_MONEY = re.compile(
    rf"(?P<sym>{_CUR})\s*(?P<num>{_NUM})|(?P<num2>{_NUM})\s*(?P<sym2>{_CUR})"
)


def _money_parts(m: re.Match) -> tuple[str, str]:
    """(symbol, number) from either arm of `_MONEY`."""
    if m.group("sym"):
        return m.group("sym"), m.group("num")
    return m.group("sym2"), m.group("num2")
# Two kinds of "not this price", because they need different strengths.
#
# HARD: the money belongs to a *different product* (a cross-sell, a recommendation, a review
# excerpt). No amount of price-ish vocabulary redeems it — a cross-sell tile's price element
# is legitimately called "price", it is just not this product's price.
_MONEY_EXCLUDE_HARD = re.compile(
    r"cross[-_ ]?sell|up[-_ ]?sell|related|recommend|similar|also[-_ ]?(?:bought|viewed|like)|"
    r"you[-_ ]?may|complete[-_ ]?the|review|question|answer|footer|nav|menu|breadcrumb|"
    r"gift[-_ ]?card|carousel",
    re.I,
)
# SOFT: the money is probably not a price at all (a shipping threshold, a loyalty accrual, an
# instalment). Overridden when the element's *own* vocabulary says it is a price, because
# retailers nest their price block inside loyalty and promotion wrappers all the time.
_MONEY_EXCLUDE_SOFT = re.compile(
    r"shipping|delivery|reward|loyalty|bonus|coupon|promo|banner|financ|instal|afterpay|"
    r"klarna|affirm|subscribe|save[-_ ]?up",
    re.I,
)
_PRICEY_CONTEXT = re.compile(r"price|pricing|amount|cost|msrp|sale", re.I)


_GROUPING_SPACE = str.maketrans("", "", "\u00a0\u202f\u2009 ")


def _parse_localized(raw: str) -> float | None:
    """A price-shaped string in *any* grouping convention.

    The previous version stripped "," unconditionally, which is correct only in en/US-shaped
    locales. Most of the world uses "," as the decimal mark, so `"349,00"` was read as 34900 —
    a hundredfold error that no downstream check could catch, and `"1.234,56"` as 1.23456.

    The rule here is positional rather than locale-tagged, because a page rarely tells us its
    number locale and we must never guess one from the domain:

      * both "." and "," present  -> the LAST one is the decimal mark, the other is grouping;
      * one of them, appearing more than once -> grouping ("1.234.567");
      * one of them, exactly three digits after it -> grouping ("1,234" / "1.234"); three is
        the only group size that is ambiguous, and grouping is overwhelmingly more likely than
        a price quoted to three decimal places;
      * otherwise -> decimal mark ("349,00", "10.24").
    """
    text = raw.strip().translate(_GROUPING_SPACE)
    if not text or not re.fullmatch(r"-?[0-9][0-9.,]*", text):
        return None
    dot, comma = text.rfind("."), text.rfind(",")
    if dot >= 0 and comma >= 0:
        dec = "." if dot > comma else ","
    elif dot >= 0 or comma >= 0:
        sep = "." if dot >= 0 else ","
        after = len(text) - text.rfind(sep) - 1
        dec = "" if text.count(sep) > 1 or after == 3 else sep
    else:
        dec = ""
    if dec:
        text = text.replace("." if dec == "," else ",", "").replace(dec, ".")
    else:
        text = text.replace(".", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _as_amount(raw) -> float | None:
    """A price-shaped value, whatever type the page chose to ship it as."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        value = _parse_localized(raw)
        if value is None:
            return None
    else:
        return None
    # A zero or absurd price is a placeholder, never a product price.
    if value <= 0 or value > 10_000_000:
        return None
    return value


def _currency_near(node, default: str | None = None) -> str | None:
    for key in _CURRENCY_KEYS:
        raw = node.get(key)
        if isinstance(raw, str) and re.fullmatch(r"[A-Za-z]{3}", raw.strip()):
            return raw.strip().upper()
        if isinstance(raw, str) and raw.strip() in _SYMBOL_CURRENCY:
            return _SYMBOL_CURRENCY[raw.strip()]
    return default


# `lowPrice`/`highPrice` are the endpoints of an AggregateOffer's *range*, not offers in their
# own right. Treated as offers they win every "cheapest wins" tie-break, which on a marketplace
# means publishing a third party's out-of-stock listing instead of the buy box.
_RANGE_KEYS = {"lowprice", "highprice"}
# schema.org hangs shipping, delivery and tax charges *inside* the Offer, as their own typed
# nodes wrapping a MonetaryAmount. A recursive read of the offers subtree therefore walks
# straight into them, and `value` is a price-shaped key, so a $4.99 delivery rate is
# indistinguishable from a $4.99 product — until you notice the product costs $6.00.
_CHARGE_TYPES = re.compile(
    r"shipping|delivery|handling|tax|fee|installment|subscription|warranty|return", re.I)
_CHARGE_PATH = re.compile(
    r"shipping|delivery|handling|\btax\b|estimatedcost|chargespecification|"
    r"returnpolicy|warranty|financ", re.I)


def _is_charge_node(path: str, node: dict) -> bool:
    """True when this subtree prices something other than the product itself."""
    if _CHARGE_PATH.search(path or ""):
        return True
    declared = node.get("@type") or node.get("type")
    return bool(isinstance(declared, str) and _CHARGE_TYPES.search(declared))
_OUT_OF_STOCK = re.compile(r"out_?of_?stock|sold_?out|discontinued|back_?order", re.I)


def _offer_stock(node) -> bool | None:
    """Tri-state: False only when the offer *says* it cannot be bought, None when it is silent.

    Silence is not a negative signal — most offers omit availability entirely — so an unknown
    offer stays eligible. Only an explicit refusal demotes one.
    """
    for key in ("availability", "availabilityStatus", "inStock", "inventoryStatus", "stockStatus"):
        raw = node.get(key)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.strip():
            return not _OUT_OF_STOCK.search(raw)
    return None


def _offer_prices(node) -> list[tuple[float, str | None, str, bool, bool | None]]:
    """Prices stated by any `offers` subtree of a node, tagged with how to weigh them.

    Recursion matters: schema.org lets a ProductGroup put its offers one level down, inside
    each `hasVariant`, so a non-recursive read finds no price at all on those pages.

    Each row carries whether it is a range endpoint and whether its offer is buyable, because
    "the lowest number in the offers subtree" is the wrong answer on any page that lists more
    than one seller.
    """
    out: list[tuple[float, str | None, str, bool, bool | None]] = []
    offers = node.get("offers") or node.get("offer") or node.get("hasVariant")
    if offers is None:
        return out
    for path, sub in walk(offers):
        if not isinstance(sub, dict):
            continue
        if _is_charge_node(path, sub):
            continue
        stock = _offer_stock(sub)
        for key in _PRICE_KEYS:
            amount = _as_amount(sub.get(key))
            if amount is not None:
                where = f"offers.{path}.{key}" if path else f"offers.{key}"
                out.append((amount, _currency_near(sub), where, key.lower() in _RANGE_KEYS, stock))
                break
    return out


def _node_prices(node) -> list[tuple[float, str | None, str, bool, bool | None]]:
    out = []
    stock = _offer_stock(node)
    for key in _PRICE_KEYS:
        amount = _as_amount(node.get(key))
        if amount is not None:
            out.append((amount, _currency_near(node), key, key.lower() in _RANGE_KEYS, stock))
    return out


def _compare_in(node, price: float) -> float | None:
    """A was-price stated by this node or its offers, if it is credibly one.

    Credible means: strictly above the selling price, and not more than an order of magnitude
    above it. A "list price" ten times the selling price is a data-entry artefact, not a
    markdown, and publishing it would misrepresent the discount.
    """
    best = None
    for _path, sub in walk(node):
        if not isinstance(sub, dict):
            continue
        for key, raw in sub.items():
            if not _COMPARE_KEY.search(str(key)):
                continue
            candidate = _as_amount(raw)
            if candidate and price < candidate <= price * 10:
                # Prefer the lowest credible was-price: it is the one the page actually struck
                # through, where a higher one is usually a manufacturer's suggested price.
                best = candidate if best is None else min(best, candidate)
    return best


def resolve_price_from_graph(nodes) -> tuple[float | None, str | None, float | None, str | None]:
    """Price, currency, compare-at — from structured data only."""
    for _score, path, node in nodes:
        found = _offer_prices(node) or _node_prices(node)
        if not found:
            continue
        # Narrow before choosing, in two steps. Concrete offers beat range endpoints, and
        # buyable offers beat ones the page says are unavailable. Only then does "lowest wins"
        # apply — and within one seller's size range, lowest is the advertised "from" price,
        # which is what a shopper is quoted.
        concrete = [f for f in found if not f[3]] or found
        buyable = [f for f in concrete if f[4] is not False] or concrete
        amount, currency, where = buyable[0][0], buyable[0][1], buyable[0][2]
        if len(buyable) > 1:
            amount = min(a for a, _c, _w, _r, _s in buyable)
        compare = _compare_in(node, amount)
        currency = currency or next((c for _a, c, _w, _r, _s in buyable if c), None)
        return amount, currency, compare, f"json:{path}.{where}" if path else f"json:{where}"
    return None, None, None, None


def _product_container(doc: Document):
    """The DOM subtree that holds the product's own buying information.

    Anchored on the page's main heading, which is where the product title lives on every
    PDP by construction (it is what the page is about). We then climb until the subtree is
    big enough to include the price block but stop well short of `<body>`.
    """
    heading = doc.tree.css_first("h1")
    if heading is None:
        return None
    chain = list(ancestors(heading, limit=8))
    return chain[-1] if chain else heading


def resolve_price_from_dom(doc: Document) -> tuple[float | None, str | None, float | None, str | None]:
    """Last resort, and a first-class source: some pages state the price only as rendered text.

    Everything found here is scoped to the product container and filtered through merchandising
    vocabulary, because an unscoped money regex on a PDP is a coin flip.
    """
    container = _product_container(doc)
    if container is None:
        return None, None, None, None
    heading = doc.tree.css_first("h1")
    heading_chain = [id(a) for a in ancestors(heading)] if heading else []

    candidates: list[tuple[int, float, str]] = []
    for node in container.css("*"):
        if node.tag in ("script", "style", "noscript", "template"):
            continue
        own_text = "".join(
            child.text() for child in node.iter(include_text=True) if child.tag == "-text"
        )
        if not own_text.strip():
            continue
        own_words = " ".join(
            f"{k}:{v}" for k, v in node.attributes.items()
            if k in ("class", "id", "data-testid", "aria-label") and v
        ).lower()
        words = context_words(node, limit=6)
        if _PLACEHOLDER_CONTEXT.search(words) or _MONEY_EXCLUDE_HARD.search(words):
            continue
        if _MONEY_EXCLUDE_SOFT.search(words) and not _PRICEY_CONTEXT.search(own_words):
            continue
        for m in _MONEY.finditer(own_text):
            symbol, number = _money_parts(m)
            amount = _as_amount(number)
            if amount is None:
                continue
            token = symbol.strip().rstrip(".").upper()
            currency = _SYMBOL_CURRENCY.get(token, token if len(token) == 3 else None)
            own_ancestors = {id(a) for a in ancestors(node)}
            # Distance to the product title, measured in shared ancestors: the closer the
            # money is to the thing the page is about, the more likely it is its price.
            distance = next(
                (i for i, a in enumerate(heading_chain) if a in own_ancestors), 99
            )
            if not _PRICEY_CONTEXT.search(own_words):
                distance += 2
            candidates.append((distance, amount, currency or ""))
    if not candidates:
        return None, None, None, None

    best = min(c[0] for c in candidates)
    tight = [c for c in candidates if c[0] <= best]
    amounts = sorted({c[1] for c in tight})
    currency = next((c[2] for c in tight if c[2]), None)
    price = amounts[0]
    # Two distinct prices inside one product-scoped price block is the universal rendering of a
    # markdown: the sale price and the struck-through original. We only ever claim a
    # compare-at price when both came from the same scope, and never invent one.
    compare = amounts[-1] if len(amounts) > 1 else None
    return price, currency, compare, "dom:product-scope"


def resolve_price(doc: Document, nodes) -> tuple[float | None, str | None, float | None, str | None]:
    """Structured data first, rendered text second — and the two cross-check each other.

    When the graph gives a price but no was-price, we look at the DOM anyway: a markdown is
    frequently rendered (struck-through original beside the sale price) without ever appearing
    in the page's JSON. We adopt it *only* when the DOM independently agrees about the selling
    price, which makes it a corroborated reading rather than an invented discount.
    """
    price, currency, compare, source = resolve_price_from_graph(nodes)
    dom_price, dom_currency, dom_compare, _ = resolve_price_from_dom(doc)
    if price is None:
        return dom_price, dom_currency, dom_compare, "dom:product-scope" if dom_price else None
    if compare is None and dom_compare and dom_price == price:
        compare = dom_compare
        source = f"{source}+dom:compare-at"
    return price, currency or dom_currency, compare, source
