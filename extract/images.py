"""Step 5 — every image of *this* product, at the largest resolution the page offers.

This is the field that most repays not asking a model. Image URLs are long, high-entropy
strings; a language model emitting them truncates them, silently rewrites a path segment, or
drops half the gallery — and you cannot tell from the output that it happened. Every URL here
is copied verbatim out of the page by Python. The model's only involvement, later, is
selecting *indices* into this list.

Three observations did most of the work:

* **Renditions are siblings; assets are array elements.** A CDN ships one photograph at eight
  sizes. If the sizes are sibling keys of one object (`{small: ..., large: ...}`) they are one
  asset; if they are elements of a list they are different photographs. That distinction, not
  URL similarity, is what makes gallery counting come out right.
* **Aspect-ratio crops are not extra images.** Some platforms ship a square and a portrait crop
  of every gallery shot. Counting both doubles the gallery.
* **SKU tokens over-filter.** A page's identity (its canonical URL segments) is embedded in
  asset paths and is safe to match against URLs. A *variant* identity (sku, mpn) is not — a
  gallery routinely spans several item ids — so those are matched only against dict keys, where
  they legitimately address a sub-object.

Resolution order: identify the page, collect every image-bearing value in the dereferenced
graph plus the markup, drop merchandising modules, scope to this product through progressively
weaker claims, group renditions, then take the largest of each group.
"""

from __future__ import annotations

import collections
import html as html_mod
import re
from urllib.parse import urljoin

from .dom import Document, context_words
from .graph import walk, score

IMG_EXT = re.compile(r"\.(?:jpe?g|png|webp|avif|gif|bmp|tiff?)(?:$|[?#])", re.I)
# Keys whose value is conventionally an image URL.
IMG_KEYS = {
    "url", "src", "srcset", "href", "image", "imageurl", "image_url", "img", "thumbnail",
    "thumb", "squarish", "portrait", "landscape", "hero", "picture", "photo", "source",
    "contenturl", "secure_url",
}
# Path or URL segments that mean "not this product". Generic merchandising vocabulary, chosen
# so it describes what a module *is* rather than what any site calls it.
EXCLUDE = re.compile(
    r"related|recommend|similar|cross[-_]?sell|up[-_]?sell|also[-_]?(?:bought|viewed|like)"
    r"|you[-_]?may|complete[-_]?the|carousel|banner|promo|nav|menu|footer|header|breadcrumb"
    r"|badge|icon|logo|payment|social|review|question|answer|swatch[-_]?nav"
    r"|fitguide|sizechart|size[-_]?guide|placeholder|sprite",
    re.I,
)
# Rendition ladder, worst to best. Used when renditions are sibling keys.
LADDER = [
    "micro", "mini", "xsmall", "tiny", "thumb", "thumbnail", "small", "preview", "medium",
    "standard", "default", "normal", "large", "xlarge", "xxlarge", "big", "full", "zoom",
    "max", "maximum", "original", "orig", "master", "raw", "hires", "hi_res", "highres",
]
LADDER_RANK = {k: i for i, k in enumerate(LADDER)}
# Keys that wrap renditions rather than naming separate assets.
CONTAINER_WORDS = {
    "sources", "source", "properties", "renditions", "sizes", "formats", "variants",
    "images", "urls", "url", "src", "srcset", "thumbnail", "crops", "versions",
}
CROP_WORDS = {
    "squarish", "square", "portrait", "landscape", "wide", "tall", "vertical", "horizontal",
    "cropped", "uncropped", "original_ratio",
}
CONTAINER_WORDS |= CROP_WORDS


def _is_img_url(value) -> bool:
    if not isinstance(value, str) or len(value) < 8:
        return False
    # A data: URI is by construction not a link to a full-resolution asset — it is an inline
    # blur-up placeholder a few kilobytes long. Emitting one would satisfy "has images" while
    # failing the actual requirement.
    if value.startswith("data:"):
        return False
    if not (value.startswith("http") or value.startswith("//")):
        return False
    lowered = value.lower()
    return bool(IMG_EXT.search(value)) or "/image" in lowered or "/img" in lowered or "cdn" in lowered


def _norm(url: str, base: str | None = None) -> str:
    url = html_mod.unescape(url.strip())
    if url.startswith("//"):
        return "https:" + url
    if base and not url.startswith("http"):
        return urljoin(base, url)
    return url


def _split_srcset(value: str) -> list[str]:
    """A single string value may be a whole srcset — in JSON as well as in markup."""
    if value.count("http") > 1 or re.search(r"\s\d+[wx]\s*(?:,|$)", value):
        return [part.split()[0] for part in value.split(",") if part.strip() and part.strip().split()]
    return [value]


def identity_tokens(doc: Document, graph_nodes) -> tuple[list[str], list[str]]:
    """Two classes of identity, deliberately kept apart (see module docstring)."""
    url_tokens, key_tokens = [], []
    for url in (doc.canonical_url(), doc.meta("url")):
        if not url:
            continue
        for seg in html_mod.unescape(url).split("?")[0].rstrip("/").split("/"):
            if seg and re.search(r"\d", seg) and seg.lower() not in {"p", "product", "products"}:
                url_tokens.append(seg)
    for _path, node in graph_nodes:
        for key in ("sku", "mpn", "productID", "productGroupID", "itemId", "styleCode", "id"):
            value = node.get(key) if isinstance(node, dict) else None
            if isinstance(value, str) and 2 < len(value) < 40 and not value.startswith("http") \
                    and re.search(r"\d", value):
                key_tokens.append(value)
    dedupe = lambda xs: list(dict.fromkeys(xs))
    return dedupe(url_tokens)[:8], dedupe(key_tokens)[:12]


def collect(blob, base: str | None) -> list[tuple[str, str, bool]]:
    """`(path, url, came_from_dict_key)` for every image-bearing value in a blob."""
    found: list[tuple[str, str, bool]] = []

    def visit(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f"{path}.{k}"
                if isinstance(v, str) and _is_img_url(v) and (str(k).lower() in IMG_KEYS or IMG_EXT.search(v)):
                    for one in _split_srcset(v):
                        if _is_img_url(one):
                            found.append((p, _norm(one, base), True))   # dict key -> a RENDITION
                else:
                    visit(v, p)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                p = f"{path}[{i}]"
                if isinstance(v, str) and _is_img_url(v):
                    for one in _split_srcset(v):
                        if _is_img_url(one):
                            found.append((p, _norm(one, base), False))  # list element -> an ASSET
                else:
                    visit(v, p)

    visit(blob)
    return found


def _dims(url: str) -> int:
    """Whatever pixel dimensions the URL itself declares."""
    best = 0
    for m in re.finditer(r"[?&](?:w|wid|width|hei|height|sz)=(\d{2,5})", url):
        best = max(best, int(m.group(1)))
    for m in re.finditer(r"/(\d{3,5})x(\d{3,5})/", url):
        best = max(best, int(m.group(1)))
    for m in re.finditer(r"[-_](\d{3,5})x(\d{1,5})(?:\.|$|[-_])", url):
        best = max(best, int(m.group(1)))
    return best


def _identity_key(url: str) -> str:
    """Collapse renditions of the same asset to one key, by URL alone."""
    key = url.split("?")[0]
    # `/<transforms>/<uuid>/<filename>` — key on the identity pair, not on stripping transforms,
    # because the transform segment is where the size lives and stripping it loses the ladder.
    m = re.search(r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/([^/]+)$", key)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    key = re.sub(
        r"[-_](?:thumb|thumbnail|mini|micro|small|medium|large|xlarge|full|max|square|standard"
        r"|preview|zoom|orig|original|retina|\d{2,4}x\d{1,4})(?=\.\w{2,5}$)", "", key, flags=re.I)
    key = re.sub(r"(?:Small|Medium|Large|XLarge|XSmall)\d*(?:Retina)?(?=\.\w{2,5}$|$)", "", key)
    return key


def _path_segments(path: str) -> set[str]:
    return set(re.split(r"[.\[\]]+", path))


def _asset_boundary(path: str) -> str | None:
    """Where one asset ends and the next begins.

    Walk up from the leaf discarding anything that only names a *rendition* of the same asset —
    a ladder word, a container word, or an index wrapping one of those — and stop at the first
    real list element. `media[0].sources.max[0]` -> `media[0]`.
    """
    parts = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    while parts:
        last = parts[-1]
        if last.startswith("["):
            if len(parts) >= 2 and (parts[-2].lower() in LADDER_RANK or parts[-2].lower() in CONTAINER_WORDS):
                parts.pop()
                continue
            break
        if last.lower() in LADDER_RANK or last.lower() in CONTAINER_WORDS:
            parts.pop()
            continue
        break
    if not parts or not parts[-1].startswith("["):
        return None
    out = ""
    for x in parts:
        out += x if x.startswith("[") else ("." + x if out else x)
    return out


def _container(path: str) -> str | None:
    """The array a gallery lives in: the asset boundary with its index removed."""
    boundary = _asset_boundary(path)
    return re.sub(r"\[\d+\]$", "", boundary) if boundary else None


def _ladder_rank(path: str) -> int:
    """Rank by the last segment that actually names a size.

    Bare indices are skipped, and an aspect-ratio crop ranks below any real size word so that
    `max` beats `square`.
    """
    for seg in reversed(re.findall(r"[^.\[\]]+", path)):
        s = seg.lower()
        if s.isdigit():
            continue
        if s in CROP_WORDS:
            return -1
        if s in LADDER_RANK:
            return LADDER_RANK[s]
        if s in CONTAINER_WORDS:
            continue
        break
    return -1


def markup_candidates(doc: Document) -> list[tuple[str, str, bool]]:
    """Images stated in the markup. A peer source, not a fallback.

    Some pages put the whole gallery in `<img>`/`<source>` and only the hero in JSON. Unlike
    the JSON path, here we can ask a *structural* question about each candidate — what module
    is this element inside? — which is the only way to tell a gallery image from a
    "customers also bought" thumbnail when both sit on the same CDN with the same URL shape.
    """
    base = doc.canonical_url()
    out: list[tuple[str, str, bool]] = []
    for node in doc.tree.css("img, source"):
        if EXCLUDE.search(context_words(node, limit=8)):
            continue
        attrs = node.attributes
        for attr in ("srcset", "data-srcset", "src", "data-src", "data-original", "data-zoom-image"):
            raw = attrs.get(attr)
            if not raw:
                continue
            for part in raw.split(","):
                bits = part.strip().split()
                if not bits:
                    continue
                url = _norm(bits[0], base)
                if _is_img_url(url):
                    out.append((f"markup.{attr}", url, False))
    for key in ("image", "image:secure_url"):
        value = doc.meta(key)
        if value:
            url = _norm(value, base)
            if _is_img_url(url):
                out.append(("markup.og", url, False))
    return out


def preload_hints(doc: Document) -> list[str]:
    """`<link rel="preload" as="image">` — a hint about *which* image is the hero, never a URL.

    Preloading exists to improve the largest-contentful-paint metric, so sites deliberately
    preload a degraded rendition: a quality-60 thumbnail, a mid-ladder transform, even a
    decorative icon. The URL is therefore useless to us; what is valuable is the *identity* it
    points at, which tells us which gallery the page considers primary and which asset comes
    first. We keep the identity and re-resolve the URL through the ladder.
    """
    hints = []
    for node in doc.tree.css("link"):
        attrs = node.attributes
        rel = (attrs.get("rel") or "").lower()
        if "preload" not in rel and "prefetch" not in rel:
            continue
        if (attrs.get("as") or "").lower() != "image":
            continue
        href = attrs.get("href") or attrs.get("imagesrcset") or ""
        for part in href.split(","):
            bits = part.strip().split()
            if bits and _is_img_url(_norm(bits[0])):
                hints.append(_identity_key(_norm(bits[0])))
    return hints


def resolve(doc: Document, graph, debug: bool = False):
    base = doc.canonical_url()
    all_nodes = [(p, n) for blob in graph for p, n in walk(blob) if isinstance(n, dict)]
    url_tokens, key_tokens = identity_tokens(doc, all_nodes)

    candidates = list(markup_candidates(doc))
    for i, blob in enumerate(graph):
        candidates += [(f"b{i}{p}", u, d) for p, u, d in collect(blob, base)]
    candidates = [c for c in candidates if not EXCLUDE.search(c[0]) and not EXCLUDE.search(c[1])]

    # -- SCOPE: keep what belongs to THIS product ---------------------------------------
    def scoped(cands):
        """Narrowest productive tier wins; each tier is a strictly weaker claim of ownership."""
        from_json = [c for c in cands if not c[0].startswith("markup")]
        from_markup = [c for c in cands if c[0].startswith("markup")]
        best = max(all_nodes, key=lambda x: score(x[1]), default=(None, None))
        tiers = [
            ("json+url-token", [c for c in from_json if any(t in c[1] for t in url_tokens)]),
            ("json+path-key", [c for c in from_json if _path_segments(c[0]) & set(key_tokens)]),
            ("json+product-node", [c for c in from_json if best[0] and c[0].startswith(best[0])]),
            ("json-all", from_json),
            ("markup+url-token", [c for c in from_markup if any(t in c[1] for t in url_tokens)]),
            ("markup-all", from_markup),
        ]
        for label, tier in tiers:
            if len(tier) >= 2:
                return tier, label
        for label, tier in tiers:
            if tier:
                return tier, label
        return cands, "none"

    candidates, how = scoped(candidates)

    # A gallery is an array, so prefer the best single container over a URL-by-URL filter.
    containers = collections.defaultdict(set)
    for path, _url, _d in candidates:
        c = _container(path)
        if c:
            containers[c].add(_asset_boundary(path))
    hints = set(preload_hints(doc))
    if containers:
        def rank(c):
            members = [x for x in candidates if _container(x[0]) == c]
            # A container holding the image the page chose to preload is the primary gallery.
            preloaded = any(_identity_key(u) in hints for _p, u, _d in members)
            return (bool(_path_segments(c) & set(key_tokens)), preloaded, len(containers[c]))
        best_container = max(containers, key=rank)
        if len(containers[best_container]) >= 2:
            picked = [c for c in candidates if _container(c[0]) == best_container]
            if picked:
                candidates, how = picked, f"{how}>container"

    # -- GROUP renditions, PICK the largest of each --------------------------------------
    groups = collections.defaultdict(list)
    for path, url, _from_dict in candidates:
        groups[_asset_boundary(path) or _identity_key(url)].append((path, url))
    chosen = []
    for _key, members in groups.items():
        ranked = sorted(members, key=lambda pu: (_ladder_rank(pu[0]), _dims(pu[1])))
        chosen.append(ranked[-1][1])

    # Structural grouping can still leave two renditions of one asset in different containers;
    # a final pass by URL identity keeps only the largest of each.
    best_per_asset: dict[str, str] = {}
    for url in chosen:
        k = _identity_key(url)
        if k not in best_per_asset or _dims(url) > _dims(best_per_asset[k]):
            best_per_asset[k] = url
    seen, final = set(), []
    for url in chosen:
        if best_per_asset.get(_identity_key(url)) == url and url not in seen:
            seen.add(url)
            final.append(url)

    # Order the gallery so the hero the page preloaded comes first.
    if hints:
        final.sort(key=lambda u: _identity_key(u) not in hints)
    if debug:
        return final, how, url_tokens
    return final
