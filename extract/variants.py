"""Step 7 — variants, derived in Python rather than requested from the model.

Variant data is the most voluminous structured thing on a PDP: one sampled page carries 25
variant rows, another carries 48 SKUs across two fits. Asking a model to re-emit that is the
single most expensive mistake available — output tokens cost roughly eight times input tokens,
and long structured output is exactly where models truncate and confabulate. Everything here
is derived deterministically. The model's entire contribution to this field, later, is
normalising axis *names* ("colour" -> "Color"), which costs about fifty tokens.

The hard part is that platforms disagree about where variants live. Five generic rules cover
what we have seen, and each is a published convention rather than a site rule:

1. schema.org `hasVariant` / `variesBy` — the standard.
2. Shopify-style `option1/2/3` against a parent `options` name list.
3. `selectedOptions` / `attributes` as `[{name, value}]` pairs, or as an `{axis: value}` map.
4. A row key that names an axis and points at an id (`sizeId`), with the label in the row's
   own `name`/`label`.
5. The containing key itself naming an axis (`sizes: [...]`, `colors: [...]`).

Scoping is the other hard part, and it matters more than detection: a PDP's "you may also
like" rail contains variant tables with *exactly the same shape* as the product's own. We
require a candidate table to sit under a node that carries this page's identity, and drop
anything under merchandising vocabulary.
"""

from __future__ import annotations

import re

from .graph import walk
from .images import _is_img_url, _norm

# Merchandising modules whose contents are *other products*. Deliberately a different list
# from the image resolver's: that one excludes `question`/`answer`/`review` because a Q&A
# module's photos are not product photos, but here a question-and-answer table is one of the
# ways a page legitimately models its options, so excluding it would throw away real variants.
EXCLUDE = re.compile(
    r"related|recommend|similar|cross[-_]?sell|up[-_]?sell|also[-_]?(?:bought|viewed|like)"
    r"|you[-_]?may|complete[-_]?the|carousel|nav|menu|footer|header|breadcrumb|promo|banner"
    r"|review|bundle|outfit|lookbook",
    re.I,
)
# Keys that mark an object as a *product* rather than an *axis*. An axis has a name and a list
# of choices; it does not have its own price, description or canonical URL. This is what keeps
# a "you may also like" rail — whose rows have a name and a list of sizes, exactly like an
# axis — from being read as a variant axis.
_PRODUCT_KEYS = {
    "price", "prices", "description", "url", "brand", "image", "images", "media",
    "currency", "slug", "canonicalurl", "categoryuri", "producturl", "offers",
}

# Axis vocabulary — the dimensions physical products vary along. Generic across retail;
# nothing here is specific to a site or a platform.
AXIS_WORDS = {
    "color": "Color", "colour": "Color", "size": "Size", "fit": "Fit", "style": "Style",
    "material": "Material", "pattern": "Pattern", "width": "Width", "length": "Length",
    "flavor": "Flavor", "flavour": "Flavor", "scent": "Scent", "capacity": "Capacity",
    "volume": "Volume", "voltage": "Voltage", "wattage": "Wattage", "finish": "Finish",
    "gauge": "Gauge", "configuration": "Configuration",
    "inseam": "Inseam", "waist": "Waist", "cup": "Cup", "band": "Band", "shade": "Shade",
}
# Deliberately NOT axes: `count`, `quantity`, `weight`, `model`. They are commerce words, but
# on a real page they are almost always a stock count or a shipping weight, and admitting them
# turns any product with an inventory field into a product with a spurious "Count: 0" axis.
# Where a row states its own human-readable label.
LABEL_KEYS = ("name", "label", "title", "value", "displayName", "display_name",
              "localizedLabel", "text", "optionValue")
# Where a row states its identity.
ID_KEYS = ("sku", "skuId", "merchSkuId", "item", "itemId", "id", "code", "partNumber",
           "variantId", "productCode", "styleCode")
GTIN_KEYS = ("gtin", "gtin8", "gtin12", "gtin13", "gtin14", "ean", "upc", "isbn", "barcode")
STOCK_KEYS = ("availability", "inStock", "in_stock", "available", "stock", "status",
              "quantity", "inventoryQuantity", "stockLevel")

_AXIS_ID_KEY = re.compile(r"^([a-z]+?)(?:id|_id|code|key)$", re.I)
_PLURAL = re.compile(r"(?:es|s)$", re.I)
_OUT_WORDS = re.compile(r"out.?of.?stock|sold.?out|unavailable|discontinued|inactive|soldout", re.I)
_IN_WORDS = re.compile(r"in.?stock|instock|available|active|backorder", re.I)
_PREORDER = re.compile(r"pre.?order|pre.?sale", re.I)

_MIN_ROWS = 2
_MAX_ROWS = 400
# Keys under which a page lists the *choices* for an axis, when it models options as a
# question-and-answer table rather than as a list of SKUs.
CHOICE_KEYS = ("answers", "values", "choices", "options", "items", "variants", "swatches",
               "selections", "terms")
# Where a choice declares which SKUs it applies to. These lists are what let us join the axes
# back into concrete variants without inventing a single combination.
SKU_SET_KEYS = ("skus", "skuIds", "sku_ids", "variantIds", "productIds", "itemIds")


def _is_null_choice(value: str) -> bool:
    """`0`, `-`, `none` are inventory sentinels, never something a shopper picks."""
    v = value.strip().lower()
    return v in {"", "0", "-", "--", "n/a", "na", "none", "null", "false", "true"}


def _label(row: dict) -> str | None:
    for key in LABEL_KEYS:
        value = row.get(key)
        if isinstance(value, str) and 0 < len(value.strip()) <= 80:
            return value.strip()
    return None


def _scalar(value):
    """A value that might be a string, or an object wrapping one."""
    if isinstance(value, str) and value.strip():
        return value.strip()[:80]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        for key in LABEL_KEYS:
            got = value.get(key)
            if isinstance(got, str) and got.strip():
                return got.strip()[:80]
    return None


def _axis_from_key(key: str) -> str | None:
    """`color` -> Color; `sizeId` -> Size; `sizes` -> Size."""
    k = str(key).strip().lower()
    if k in AXIS_WORDS:
        return AXIS_WORDS[k]
    m = _AXIS_ID_KEY.match(k)
    if m and m.group(1) in AXIS_WORDS:
        return AXIS_WORDS[m.group(1)]
    singular = _PLURAL.sub("", k)
    if singular in AXIS_WORDS:
        return AXIS_WORDS[singular]
    return None


def _row_options(row: dict, parent: dict, container_axis: str | None) -> dict[str, str]:
    """Every `{axis: value}` this row states, by any of the five conventions."""
    options: dict[str, str] = {}

    # (1) the axis is a property of the row.
    for key, value in row.items():
        axis = _axis_from_key(key)
        if not axis or _AXIS_ID_KEY.match(str(key)):
            continue
        scalar = _scalar(value)
        if scalar and not _is_null_choice(scalar):
            options.setdefault(axis, scalar)

    # (2) positional options against the parent's name list.
    names = parent.get("options") or parent.get("optionNames") or parent.get("variesBy")
    if isinstance(names, list) and names:
        labels = [_scalar(n) for n in names]
        for i, label in enumerate(labels, start=1):
            value = _scalar(row.get(f"option{i}"))
            if label and value:
                # schema.org spells `variesBy` as a URL (`https://schema.org/color`).
                clean = str(label).rstrip("/").split("/")[-1]
                options.setdefault(_axis_from_key(clean) or clean.title(), value)

    # (3) explicit name/value pairs, in either of the two JSON spellings. The same key names
    # carry the same meaning as a list of `{name, value}` objects *or* as a plain
    # `{axis: value}` map — schema.org and the platform APIs are split roughly evenly, and
    # reading only the list form loses every map-shaped catalog.
    for key in ("selectedOptions", "options", "attributes", "variantValues", "specs", "properties"):
        pairs = row.get(key)
        if isinstance(pairs, dict):
            for name, raw in pairs.items():
                value = _scalar(raw)
                name = str(name).strip()
                if name and value and name.lower() != value.lower() and not _is_null_choice(value):
                    options.setdefault(_axis_from_key(name) or name.title(), value)
            continue
        if not isinstance(pairs, list):
            continue
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            name = _scalar(pair.get("name") or pair.get("key") or pair.get("attribute"))
            value = _scalar(pair.get("value") or pair.get("optionValue") or pair.get("label"))
            if name and value and name.lower() != value.lower():
                options.setdefault(_axis_from_key(name) or name.title(), value)

    # (4)/(5) the axis is named by an id-key or by the containing array, and the value is the
    # row's own label. Only used when the row has not already stated that axis explicitly.
    if not options:
        axis = container_axis
        for key in row:
            got = _axis_from_key(key) if _AXIS_ID_KEY.match(str(key)) else None
            if got:
                axis = got
                break
        label = _label(row)
        if axis and label:
            options[axis] = label
    return options


def _sku_set(row: dict) -> list[str]:
    for key in SKU_SET_KEYS:
        value = row.get(key)
        if isinstance(value, list) and value:
            ids = [v for v in value if isinstance(v, str) and 1 < len(v) < 60]
            if ids:
                return ids
    return []


def _axis_definitions(graph, identity_tokens, product_name):
    """Rule 6 — the axis-definition table.

    Some platforms do not ship a list of SKUs at all. They ship the *questions*: a list of
    axes, each with its list of answers, and each answer declaring which SKUs it selects. That
    is a perfectly good description of the variant space, and it is richer than a SKU list in
    one respect — the answers carry the axis names explicitly.

    Where the answers declare SKU sets we can recover the concrete variants exactly, by
    intersection: the SKU that appears under "Black" and under "Tall" and under "Medium" *is*
    the Black/Tall/Medium variant. No combination is invented; a combination that no SKU
    satisfies simply does not appear.
    """
    best: list[tuple[str, list[tuple[str, list[str]]]]] = []
    for i, blob in enumerate(graph):
        for path, node in walk(blob):
            for key, value in node.items():
                if not isinstance(value, list) or not (1 <= len(value) <= 20):
                    continue
                full = f"b{i}.{path}.{key}" if path else f"b{i}.{key}"
                if EXCLUDE.search(full):
                    continue
                axes: list[tuple[str, list[tuple[str, list[str]]]]] = []
                for row in value:
                    if not isinstance(row, dict):
                        continue
                    axis_label = _label(row)
                    if not axis_label or len(axis_label) > 40:
                        continue
                    if len({str(k).lower() for k in row} & _PRODUCT_KEYS) >= 2:
                        continue        # this row is a product, not an axis
                    choices = None
                    for choice_key in CHOICE_KEYS:
                        got = row.get(choice_key)
                        if isinstance(got, list) and sum(isinstance(c, dict) for c in got) >= 2:
                            choices = [c for c in got if isinstance(c, dict)]
                            break
                    if not choices:
                        continue
                    labelled = [(_label(c), _sku_set(c)) for c in choices]
                    labelled = [(lab, skus) for lab, skus in labelled if lab and not _is_null_choice(lab)]
                    if len(labelled) >= 2:
                        axes.append((axis_label, labelled))
                # Only claim an axis table when the page names at least one axis we recognise.
                # Without this gate any list of products-with-sizes reads as a list of axes.
                if len(axes) > len(best) and any(_axis_from_key(a) for a, _c in axes):
                    best = axes
    return best


def _availability(row: dict) -> str:
    for key in STOCK_KEYS:
        if key not in row:
            continue
        value = row[key]
        if isinstance(value, bool):
            return "in_stock" if value else "out_of_stock"
        if isinstance(value, (int, float)):
            return "in_stock" if value > 0 else "out_of_stock"
        if isinstance(value, str):
            if _PREORDER.search(value):
                return "preorder"
            if _OUT_WORDS.search(value):
                return "out_of_stock"
            if _IN_WORDS.search(value):
                return "in_stock"
    # Stock split across locations: available anywhere means available.
    for key in ("warehouses", "locations", "inventory", "stores"):
        rows = row.get(key)
        if isinstance(rows, list) and rows:
            total = sum(r.get("stock", 0) or 0 for r in rows if isinstance(r, dict))
            if any(isinstance(r, dict) and "stock" in r for r in rows):
                return "in_stock" if total > 0 else "out_of_stock"
    return "unknown"


def _identity(row: dict, keys) -> str | None:
    for key in keys:
        for k, v in row.items():
            if str(k).lower() != key.lower():
                continue
            scalar = _scalar(v)
            if scalar and not scalar.startswith("http"):
                return scalar
            if isinstance(v, list) and v:
                nested = _scalar(v[0])
                if nested:
                    return nested
    return None


def _row_price(row: dict):
    from .fields import _as_amount, _currency_near, _PRICE_KEYS
    for path, node in walk(row):
        if not isinstance(node, dict) or path.count(".") > 2:
            continue
        for key in _PRICE_KEYS:
            amount = _as_amount(node.get(key))
            if amount is not None:
                return amount, _currency_near(node)
    return None, None


def _row_images(row: dict) -> list[str]:
    out = []
    for _path, node in walk(row):
        if not isinstance(node, dict):
            continue
        for k, v in node.items():
            if isinstance(v, str) and _is_img_url(v) and str(k).lower() in {
                "url", "src", "image", "imageurl", "thumbnail", "photo", "contenturl"
            }:
                out.append(_norm(v))
    return list(dict.fromkeys(out))[:12]


def _candidate_tables(graph, identity_tokens: list[str], product_name: str | None):
    """Every list that looks like a table of variants, with an ownership judgement attached."""
    name_key = (product_name or "").strip().lower()
    tables = []
    for i, blob in enumerate(graph):
        for path, node in walk(blob):
            full = f"b{i}.{path}" if path else f"b{i}"
            # Does this node claim to *be* this page's product? If so, tables beneath it are
            # this product's variants; if not, they may well belong to a recommendation.
            owned = False
            node_name = _scalar(node.get("name") or node.get("title"))
            if name_key and node_name and node_name.strip().lower() == name_key:
                owned = True
            if not owned:
                for key in ("sku", "mpn", "productID", "id", "url", "canonicalUrl"):
                    value = _scalar(node.get(key))
                    if value and any(t and t in value for t in identity_tokens):
                        owned = True
                        break
            for key, value in node.items():
                if not isinstance(value, list) or not (_MIN_ROWS <= len(value) <= _MAX_ROWS):
                    continue
                rows = [r for r in value if isinstance(r, dict)]
                if len(rows) < _MIN_ROWS or len(rows) < len(value) * 0.5:
                    continue
                child_path = f"{full}.{key}"
                if EXCLUDE.search(child_path):
                    continue
                container_axis = _axis_from_key(key)
                parsed = [(_row_options(r, node, container_axis), r) for r in rows]
                if sum(1 for opts, _r in parsed if opts) < max(2, len(rows) * 0.5):
                    continue
                identified = sum(
                    1 for _o, r in parsed
                    if _identity(r, ID_KEYS) or _identity(r, GTIN_KEYS)
                )
                tables.append({
                    "path": child_path,
                    "owned": owned,
                    "rows": parsed,
                    "identified": identified,
                    "axes": {a for opts, _r in parsed for a in opts},
                })
    return tables


def _pick_tables(tables):
    """Keep the tables that describe this product; drop look-alikes from other modules."""
    if not tables:
        return []
    owned = [t for t in tables if t["owned"]]
    pool = owned or tables
    # De-duplicate: normalised caches expose the same array through several paths.
    by_signature: dict[tuple, dict] = {}
    for t in pool:
        signature = (
            tuple(sorted(t["axes"])),
            len(t["rows"]),
            tuple(sorted(str(o) for o, _r in t["rows"][:4])),
        )
        current = by_signature.get(signature)
        if current is None or len(t["path"]) < len(current["path"]):
            by_signature[signature] = t
    unique = list(by_signature.values())
    # One table per axis set: the richest (most identified rows), then the shallowest path.
    best: dict[tuple, dict] = {}
    for t in unique:
        key = tuple(sorted(t["axes"]))
        current = best.get(key)
        if current is None or (t["identified"], -len(t["path"])) > (current["identified"], -len(current["path"])):
            best[key] = t
    chosen = list(best.values())
    # A table whose axes are a strict subset of another's is that table's axis list, already
    # covered by the richer one.
    keep = []
    for t in chosen:
        if any(t["axes"] < other["axes"] for other in chosen if other is not t):
            continue
        keep.append(t)
    return keep


def derive(graph, identity_tokens: list[str], product_name: str | None,
           fallback_sku: str | None = None):
    """-> (options, variants, colors). Pure Python; no model involved."""
    tables = _pick_tables(_candidate_tables(graph, identity_tokens, product_name))
    definitions = _axis_definitions(graph, identity_tokens, product_name)

    variants: list[dict] = []
    seen_keys: set[tuple] = set()
    for table in tables:
        for options, row in table["rows"]:
            if not options:
                continue
            key = tuple(sorted(options.items()))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            price, currency = _row_price(row)
            variants.append({
                "sku": _identity(row, ID_KEYS),
                "options": options,
                "price": {"price": price, "currency": currency or "USD", "compare_at_price": None}
                          if price is not None else None,
                "image_urls": _row_images(row),
                "availability": _availability(row),
                "gtin": _identity(row, GTIN_KEYS),
                "mpn": _identity(row, ("mpn", "manufacturerPartNumber")),
            })

    # Rule 6's axes, and the SKU join that turns them back into concrete variants.
    if definitions and len({a for a, _c in definitions}) > len({a for v in variants for a in v["options"]}):
        by_sku: dict[str, dict[str, str]] = {}
        for axis, choices in definitions:
            for label, skus in choices:
                for sku in skus:
                    by_sku.setdefault(sku, {})[axis] = label
        complete = {a for a, _c in definitions}
        joined = [
            {"sku": sku, "options": opts, "price": None, "image_urls": [],
             "availability": "unknown", "gtin": None, "mpn": None}
            for sku, opts in by_sku.items() if set(opts) == complete
        ]
        if joined:
            variants = joined
        else:
            # No SKU sets to join on: publish the axes, and say nothing about which
            # combinations exist rather than emitting a cross-product we cannot verify.
            variants = []
        definition_axes = [{"name": a, "values": [lab for lab, _s in choices]}
                           for a, choices in definitions]
    else:
        definition_axes = []

    # Axes, in the order the page presents their values — a size picker in page order reads
    # S/M/L; a size picker in sorted order reads L/M/S, which is wrong on every clothing site.
    axis_values: dict[str, list[str]] = {}
    for variant in variants:
        for axis, value in variant["options"].items():
            bucket = axis_values.setdefault(axis, [])
            if value not in bucket:
                bucket.append(value)
    options = [{"name": axis, "values": values} for axis, values in axis_values.items() if values]
    # Prefer the page's own axis declaration when it has one: it names the axes explicitly and
    # lists every value, including values whose SKUs are absent from the page.
    if definition_axes:
        derived = {o["name"]: o for o in options}
        options = [
            {"name": a["name"], "values": a["values"] or derived.get(a["name"], {}).get("values", [])}
            for a in definition_axes
        ]

    # A single-variant product still gets one row, so every consumer sees the same shape.
    if not variants:
        variants = [{
            "sku": fallback_sku, "options": {}, "price": None, "image_urls": [],
            "availability": "unknown", "gtin": None, "mpn": None,
        }]

    colors = next((o["values"] for o in options if o["name"] == "Color"), [])
    return options, variants, colors
