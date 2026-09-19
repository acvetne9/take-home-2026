"""Step 3 — the harvested blobs as one graph: traversal, reference resolution, node scoring.

Modern SSR frameworks do not ship a product object. They ship a *normalised cache*: a flat
table of rows keyed by id, with every relationship stored as a reference. The product's price
is not inside the product node, it is in row `Price:12345`, pointed at by
`product.prices -> "Price:12345"`. A stage that reads the graph without resolving references
is structurally blind to most of the page's facts — so dereferencing happens before anything
else looks at the data.

Two hard-won rules are encoded here:

* **Resolution must be cycle-safe.** A normalised cache is a cyclic graph (a variant points at
  its product, which points back at its variants). The obvious recursive resolver raises
  `RecursionError` on real pages. Hence the visited set and the depth cap.

* **String references are followed only from reference-shaped keys.** An earlier version
  followed any string that happened to match a row id, which resolved an innocent `page.id`
  into the entire page object and doubled the size of the graph — and, worse, made unrelated
  facts look like they belonged to the product.
"""

from __future__ import annotations

import re

# Keys whose value is conventionally a name/price/identity of a product. Purely generic
# commerce vocabulary; nothing here is specific to any site or platform.
_SIGNAL_KEYS = {
    "name", "title", "price", "offers", "sku", "brand", "image", "images", "description",
    "productid", "mpn", "gtin", "gtin13", "currency", "pricecurrency", "availability",
    "variants", "hasvariant", "skus", "media", "color", "colour", "size", "material",
}
_PRODUCT_TYPES = {"product", "productgroup", "productmodel", "individualproduct"}

# Which string-valued keys may point at another row. Anything else stays a plain string.
_REF_KEY = re.compile(
    r"(?:^|_)(?:ref|id|ids)$|Id$|Ids$|^(?:prices?|media|mediaIds|skus?|items?|variants?)$"
)

_MAX_DEPTH = 12
_MAX_DEREF_DEPTH = 8


def walk(obj, path: str = "", depth: int = 0):
    """Yield `(path, dict)` for every object in the tree, with a real path.

    The path keeps list indices (`media[3].url`, not `media.url`) because that is exactly what
    distinguishes one gallery asset from the next — the image resolver depends on it.
    """
    if depth > _MAX_DEPTH:
        return
    if isinstance(obj, dict):
        yield path, obj
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}" if path else str(k), depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]", depth + 1)


def score(node) -> int:
    """How product-shaped is this object?

    A count of commerce-vocabulary keys, with a large bonus for an explicit schema.org product
    type. This is used to *order* candidates, never to pick "the one true product node" — four
    successive attempts at a single best-node selector failed, because different pages put
    different fields in different objects. Fields are resolved one at a time instead.
    """
    if not isinstance(node, dict):
        return 0
    s = sum(1 for k in node if str(k).lower() in _SIGNAL_KEYS)
    node_type = str(node.get("@type", "")).lower().rstrip("/").split("/")[-1]
    if node_type in _PRODUCT_TYPES:
        s += 6
    return s


def build_store(blobs) -> dict[str, dict]:
    """`id -> row`, for every object carrying an id. The lookup table for dereferencing."""
    store: dict[str, dict] = {}
    for _carrier, blob in blobs:
        for _path, node in walk(blob):
            for key in ("id", "@id", "__id", "key"):
                ident = node.get(key)
                if isinstance(ident, str) and 1 < len(ident) < 120:
                    store.setdefault(ident, node)
    return store


def deref(obj, store: dict[str, dict], seen: frozenset[str] | None = None, depth: int = 0):
    """Expand `{"__ref": "..."}` pointers and id-valued reference keys into their rows."""
    seen = seen or frozenset()
    if depth > _MAX_DEREF_DEPTH:
        return obj
    if isinstance(obj, dict):
        ref = obj.get("__ref") or obj.get("__typename_ref")
        if isinstance(ref, str) and ref in store and ref not in seen:
            return deref(store[ref], store, seen | {ref}, depth + 1)
        return {k: _deref_field(k, v, store, seen, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [deref(v, store, seen, depth + 1) for v in obj]
    return obj


def _deref_field(key, value, store, seen, depth):
    if isinstance(value, str) and _REF_KEY.search(str(key)) and value in store and value not in seen:
        return deref(store[value], store, seen | {value}, depth + 1)
    return deref(value, store, seen, depth)


def resolve_graph(blobs) -> list[object]:
    """The harvested blobs with every reference expanded. Everything downstream reads this."""
    store = build_store(blobs)
    return [deref(blob, store) for _carrier, blob in blobs]


def product_nodes(graph) -> list[tuple[int, str, dict]]:
    """Every object in the graph, ordered by how product-shaped it is."""
    found = [
        (score(node), path, node)
        for blob in graph
        for path, node in walk(blob)
        if isinstance(node, dict)
    ]
    found.sort(key=lambda x: x[0], reverse=True)
    return found
