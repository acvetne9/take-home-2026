"""Step 1 — decode once, parse once.

Every later stage reads from one `Document`: the harvester, the field resolvers, the image
resolver and the bundler all need the same page, and reparsing 780 KB of HTML five times is
pure waste.

Two things here are deliberate rather than incidental:

1. **Decoding is explicit and fails loudly.** The usual `read_text(errors="replace")` silently
   mangles a non-UTF-8 page into replacement characters, and the damage only shows up later as
   a garbled product name. We honour the transport charset, then the document's own
   `<meta charset>`, then raise. A page we cannot decode is a page we should refuse, not guess.

2. **We keep a real DOM, not just the markup string.** Two of the highest-value extraction
   rules are tree operations and cannot be expressed correctly with regex: a Microdata
   `itemprop` binds to its *nearest ancestor* `itemscope` (a page carrying a Product, a
   BreadcrumbList and three Offers together will otherwise read the right values and attribute
   them to the wrong owner), and "is this image inside a 'related products' module?" is a
   question about ancestors.

Note on the no-site-specific-logic rule: selectors are fine, *site* selectors are not. This
module only ever asks about published vocabularies — `itemprop`, `itemscope`, `<script>`,
`rel`, `property` — never about a class name a particular retailer happens to use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from selectolax.parser import HTMLParser, Node

# <meta charset=...> / <meta http-equiv="content-type" content="...; charset=...">.
# Searched over the leading bytes only: the spec puts the declaration in the first 1024 bytes,
# and scanning a 780 KB document for it is both slower and more likely to hit a false positive
# inside page content.
_META_CHARSET = re.compile(
    rb"""<meta[^>]+?charset\s*=\s*["']?\s*([a-zA-Z0-9_\-:.]+)""", re.I
)
_SNIFF_WINDOW = 4096


class UndecodableDocument(ValueError):
    """The page's bytes are not valid in any charset we could identify."""


def _declared_charset(raw: bytes, content_type: str | None) -> list[str]:
    """Charsets to try, most authoritative first.

    Transport beats document beats default, which is the precedence the HTML standard
    specifies. UTF-8 is tried last as the modern default even when nothing is declared.
    """
    candidates: list[str] = []
    if content_type:
        m = re.search(r"charset\s*=\s*([\w\-:.]+)", content_type, re.I)
        if m:
            candidates.append(m.group(1))
    m = _META_CHARSET.search(raw[:_SNIFF_WINDOW])
    if m:
        candidates.append(m.group(1).decode("ascii", "ignore"))
    candidates.append("utf-8")

    seen, ordered = set(), []
    for c in candidates:
        key = c.lower().replace("_", "-")
        if key and key not in seen:
            seen.add(key)
            ordered.append(c)
    return ordered


def decode(raw: bytes, content_type: str | None = None) -> str:
    """Bytes -> text, strictly. Raises rather than producing mojibake."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    errors: list[str] = []
    for charset in _declared_charset(raw, content_type):
        try:
            return raw.decode(charset)
        except (UnicodeDecodeError, LookupError) as exc:
            errors.append(f"{charset}: {exc.__class__.__name__}")
    raise UndecodableDocument(
        "could not decode document; tried " + ", ".join(errors)
    )


@dataclass
class Document:
    """One page: its text, its DOM, and the few whole-document questions everyone asks."""

    html: str
    tree: HTMLParser
    source: str = ""

    @classmethod
    def from_bytes(cls, raw: bytes, content_type: str | None = None, source: str = "") -> "Document":
        return cls.from_text(decode(raw, content_type), source=source)

    @classmethod
    def from_text(cls, html: str, source: str = "") -> "Document":
        return cls(html=html, tree=HTMLParser(html), source=source)

    @classmethod
    def from_path(cls, path: str | Path) -> "Document":
        p = Path(path)
        return cls.from_bytes(p.read_bytes(), source=p.name)

    # -- whole-document lookups -------------------------------------------------------

    def meta(self, key: str) -> str | None:
        """A `<meta>` value by `property` or `name`, matched case-insensitively.

        Case matters more than it looks: HTML attribute names are ASCII case-insensitive
        (HTML Living Standard 13.1.2.3) and JSX-based frameworks emit their source spelling
        verbatim, so real pages ship `charSet` and `itemProp`. A case-sensitive matcher finds
        nothing on those pages.
        """
        key = key.lower()
        for node in self.tree.css("meta"):
            attrs = node.attributes
            for label in ("property", "name", "itemprop"):
                got = attrs.get(label)
                if got and got.strip().lower() in (key, f"og:{key}"):
                    content = attrs.get("content")
                    if content and content.strip():
                        return content.strip()
        return None

    def link(self, rel: str) -> str | None:
        """A `<link>` href by `rel`."""
        rel = rel.lower()
        for node in self.tree.css("link"):
            got = node.attributes.get("rel") or ""
            if rel in got.lower().split():
                href = node.attributes.get("href")
                if href:
                    return href.strip()
        return None

    def title(self) -> str | None:
        node = self.tree.css_first("title")
        if node and node.text():
            return node.text().strip()
        return None

    def canonical_url(self) -> str | None:
        return self.link("canonical") or self.meta("url")


# -- tree helpers shared by the harvester and the image resolver ----------------------


def ancestors(node: Node, limit: int = 40):
    """Walk up from a node. Used to ask 'what module is this inside?'."""
    cur, depth = node.parent, 0
    while cur is not None and depth < limit:
        yield cur
        cur, depth = cur.parent, depth + 1


def context_words(node: Node, limit: int = 12) -> str:
    """The class/id/aria vocabulary of a node and its ancestors, lower-cased.

    This is how we ask a *structural* question — "is this inside a recommendations module?",
    "is this a loading skeleton?" — without naming any site's classes. We match against
    generic words that the whole industry uses (`related`, `skeleton`, `aria-busy`); the fact
    that a given site spells its class `Foo_related__x1` is irrelevant, we only look for the
    word inside it.
    """
    parts: list[str] = []
    for cur in [node, *list(ancestors(node, limit))]:
        attrs = cur.attributes if cur is not None else {}
        for key in ("class", "id", "data-testid", "role", "aria-label", "aria-busy", "aria-hidden"):
            v = attrs.get(key)
            if v:
                parts.append(f"{key}:{v}")
    return " ".join(parts).lower()
