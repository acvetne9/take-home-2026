"""Step 2a — markup-embedded structured data: Microdata and RDFa.

Both are published W3C vocabularies for annotating HTML with schema.org types, and both are
read here into exactly the same dict shape that JSON-LD produces, so nothing downstream has to
know which carrier a fact came from.

Why this is worth real code rather than a regex:

* **Ownership.** A `itemprop` belongs to its *nearest ancestor* `itemscope`. A typical PDP
  carries a Product, a BreadcrumbList, several ListItems and two or three Offers in one
  document. A flat regex reads every value correctly and assigns most of them to the wrong
  owner, which produces a "price" that is really a shipping threshold or a related item's.
* **Precision, not recall.** On one sampled page the product's price appears in over a thousand
  framework-state nodes; the same page states it once, unambiguously, in a `schema.org/Offer`
  in the markup. Any "first price-shaped number wins" rule that gets that page right gets it
  right by luck. The annotation is the authoritative statement.
* **Breadcrumbs.** RDFa and Microdata breadcrumb trails are a category path written by the
  merchant — the single most on-distribution signal available for taxonomy mapping, and it is
  usually not in the JSON at all.

Attribute names are matched case-insensitively throughout: HTML attribute names are ASCII
case-insensitive, and JSX-based frameworks emit their source spelling verbatim, so real pages
ship `itemProp`/`itemType`. Case-sensitive matching finds zero annotations on those pages.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

# Per the Microdata spec, these elements state their value in an attribute rather than in text.
_VALUE_ATTR = {
    "meta": "content",
    "audio": "src", "embed": "src", "iframe": "src", "img": "src",
    "source": "src", "track": "src", "video": "src",
    "a": "href", "area": "href", "link": "href",
    "object": "data",
    "data": "value", "meter": "value",
    "time": "datetime",
}
_URL_VALUED = {"a", "area", "link", "audio", "embed", "iframe", "img", "source", "track", "video", "object"}
_MAX_TEXT = 500


def _attr(node: Node, name: str) -> str | None:
    """An attribute, matched case-insensitively, with `''` for a valueless attribute."""
    attrs = node.attributes
    got = attrs.get(name)
    if got is not None:
        return got if got is not None else ""
    for k, v in attrs.items():
        if k.lower() == name:
            return v if v is not None else ""
    return None


def _has(node: Node, name: str) -> bool:
    return _attr(node, name) is not None


def _type_name(raw: str | None) -> str:
    """`http://schema.org/Product` / `schema:Product` / `Product` -> `Product`."""
    if not raw:
        return ""
    first = raw.strip().split()[0] if raw.strip() else ""
    return first.rstrip("/").split("/")[-1].split("#")[-1].split(":")[-1]


def _value(node: Node, base_url: str | None) -> str:
    """The value an annotated element states, per the Microdata value algorithm."""
    tag = (node.tag or "").lower()
    attr = _VALUE_ATTR.get(tag)
    raw = None
    if attr:
        raw = _attr(node, attr)
    if raw is None:
        raw = _attr(node, "content")
    if raw is None:
        raw = (node.text(deep=True) or "").strip()
        raw = re.sub(r"\s+", " ", raw)
    else:
        raw = raw.strip()
        if base_url and tag in _URL_VALUED and raw:
            raw = urljoin(base_url, raw)
    return raw[:_MAX_TEXT]


def _merge(obj: dict, key: str, value) -> None:
    """schema.org properties are repeatable; collapse repeats into a list like JSON-LD does."""
    if key not in obj:
        obj[key] = value
    elif isinstance(obj[key], list):
        obj[key].append(value)
    else:
        obj[key] = [obj[key], value]


def _scope_key(scope_attr: str, type_attr: str) -> tuple[str, str]:
    return (scope_attr, type_attr)


def _extract(tree: HTMLParser, scope_attr: str, prop_attr: str, type_attr: str,
             base_url: str | None) -> list[dict]:
    """One pass of the nesting algorithm, shared by Microdata and RDFa.

    Microdata:  itemscope / itemprop / itemtype
    RDFa:       typeof (which implies a scope) / property / typeof

    We walk the DOM depth-first keeping a stack of open scopes, so a property always attaches
    to the innermost scope that encloses it — which is the whole point of doing this on a tree.
    """
    items: list[dict] = []

    def walk(node: Node, stack: list[dict]) -> None:
        if node.tag in ("-text", "_comment", None):
            for child in node.iter(include_text=False):
                walk(child, stack)
            return

        opens_scope = _has(node, scope_attr)
        prop = _attr(node, prop_attr)
        node_type = _type_name(_attr(node, type_attr))
        if scope_attr == "typeof":
            # In RDFa there is no separate scope attribute: `typeof` is the scope.
            opens_scope = bool(node_type)

        current = stack
        if opens_scope:
            item: dict = {}
            if node_type:
                item["@type"] = node_type
            itemid = _attr(node, "itemid") or _attr(node, "resource")
            if itemid:
                item["@id"] = itemid
            # A scope that is itself a property of an enclosing scope nests inside it.
            if prop and stack:
                for p in prop.split():
                    _merge(stack[-1], p.split(":")[-1], item)
            elif stack:
                _merge(stack[-1], "_child", item)
            else:
                items.append(item)
            current = stack + [item]
        elif prop and stack:
            val = _value(node, base_url)
            if val:
                for p in prop.split():
                    _merge(stack[-1], p.split(":")[-1], val)

        for child in node.iter(include_text=False):
            walk(child, current)

    root = tree.root
    if root is not None:
        walk(root, [])
    return [i for i in items if len(i) > 1]


def microdata(doc) -> list[tuple[str, dict]]:
    """schema.org Microdata, shaped like JSON-LD."""
    base = doc.canonical_url()
    return [("microdata", i) for i in _extract(doc.tree, "itemscope", "itemprop", "itemtype", base)]


def rdfa(doc) -> list[tuple[str, dict]]:
    """RDFa Lite. Same vocabulary, different attribute spelling."""
    base = doc.canonical_url()
    out = _extract(doc.tree, "typeof", "property", "typeof", base)
    return [("rdfa", i) for i in out]


# -- breadcrumbs ------------------------------------------------------------------------

_BREADCRUMB_TYPES = {"breadcrumblist", "breadcrumb"}
_TRAIL_SEPARATOR = re.compile(r"\s*(?:>|›|»|/|\|)\s*")
_BREADCRUMB_NOISE = {"home", "homepage", "all", "shop", "back", "menu", ""}


def breadcrumb_from_node(node) -> list[str]:
    """`BreadcrumbList` -> `['Tools', 'Power Tools', 'Cordless Drills']`.

    Accepts the schema.org shape from any carrier, since Microdata, RDFa and JSON-LD all
    produce the same dict here.
    """
    if not isinstance(node, dict):
        return []
    if _type_name(str(node.get("@type", ""))).lower() not in _BREADCRUMB_TYPES:
        return []
    elements = node.get("itemListElement") or node.get("_child") or []
    if isinstance(elements, dict):
        elements = [elements]
    trail: list[str] = []
    for el in elements:
        if not isinstance(el, dict):
            continue
        name = el.get("name") or el.get("title")
        if isinstance(name, dict):
            name = name.get("name")
        item = el.get("item")
        if not name and isinstance(item, dict):
            name = item.get("name")
        if isinstance(name, str) and name.strip():
            trail.append(re.sub(r"\s+", " ", name.strip()))
    return trail


def breadcrumb_from_dom(doc) -> list[str]:
    """Fallback: a nav landmark whose own accessibility vocabulary says 'breadcrumb'.

    This is a published convention (`aria-label="breadcrumb"`, `<nav>` landmarks), not a site
    rule — we never look for a particular retailer's class name.
    """
    for node in doc.tree.css("nav, ol, ul, div"):
        words = " ".join(
            f"{k}:{v}" for k, v in node.attributes.items()
            if k in ("class", "id", "aria-label", "role", "data-testid") and v
        ).lower()
        if "breadcrumb" not in words:
            continue
        parts = [
            re.sub(r"\s+", " ", (a.text() or "").strip())
            for a in node.css("a, li, span")
        ]
        trail = [p for p in parts if p and p.lower() not in _BREADCRUMB_NOISE and len(p) < 60]
        # De-duplicate while preserving order: nested <li><a> yields each label twice.
        seen, out = set(), []
        for p in trail:
            if p.lower() not in seen:
                seen.add(p.lower())
                out.append(p)
        if len(out) >= 2:
            return out[:8]
    return []
