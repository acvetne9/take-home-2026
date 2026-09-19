"""Step 9a — the evidence bundle: 780 KB of HTML compressed to roughly 2,000 tokens.

Sending raw HTML to a model costs about $463,000 per 50 million products and is *worse*, not
just dearer: the interesting facts are buried under hundreds of kilobytes of script and CSS,
and what survives the context window is arbitrary. The bundle is the alternative — a compact,
ordered brief containing only what a judgement call could need:

  1. what Python already resolved, stated as fact (so the model never re-derives it),
  2. the best structured-data candidates, dereferenced and truncated,
  3. the page's own metadata,
  4. the visible text, de-duplicated,
  5. an indexed image list, so the model can answer about images by *number*.

That last point is the important one. The model is never asked to emit a URL, a variant row or
a price. Output tokens cost roughly eight times input tokens on the small models, and long
structured output is where truncation and confabulation live. Everything the model returns here
is a short judgement: a category line copied from a list, a handful of feature bullets, a few
colour names, and some integers.
"""

from __future__ import annotations

import html as html_mod
import json
import re

from .graph import walk

_MAX_CANDIDATES = 3
_CANDIDATE_BUDGET = 2400          # characters per structured-data candidate
_TEXT_BUDGET = 3500
_META_BUDGET = 900
_IMAGE_LIST = 30

_STRIP_TAGS = re.compile(r"<(script|style|noscript|svg|template)\b.*?</\1>", re.S | re.I)
_COMMENTS = re.compile(r"<!--.*?-->", re.S)
_TAGS = re.compile(r"<[^>]+>")
# Keys that are pure plumbing: they cost tokens and carry no meaning a judgement could use.
_NOISE_KEYS = re.compile(
    r"^(?:__typename|_+id|tracking|analytics|gtm|ga|utm|telemetry|experiment|ab_?test|"
    r"session|csrf|nonce|timestamp|created_?at|updated_?at|locale|hreflang|breakpoints?|"
    r"css|style|classname|dataLayer)", re.I,
)


def visible_text(doc, limit: int = _TEXT_BUDGET) -> str:
    """The page as a reader sees it, de-duplicated.

    De-duplication is what makes this cheap: a PDP repeats its navigation, its size labels and
    its shipping copy many times over, and repeated lines carry no extra information.
    """
    html = _STRIP_TAGS.sub(" ", doc.html)
    html = _COMMENTS.sub(" ", html)
    text = html_mod.unescape(_TAGS.sub("\n", html))
    out, seen = [], set()
    for line in (l.strip() for l in text.split("\n")):
        if 1 < len(line) < 400 and line not in seen:
            seen.add(line)
            out.append(line)
    return "\n".join(out)[:limit]


def _prune(obj, depth: int = 0):
    """Drop plumbing keys and over-long strings before serialising a candidate."""
    if depth > 6:
        return None
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _NOISE_KEYS.match(str(k)):
                continue
            pruned = _prune(v, depth + 1)
            if pruned not in (None, "", [], {}):
                out[k] = pruned
        return out
    if isinstance(obj, list):
        # A long homogeneous list (48 SKUs, 25 sizes) is already handled deterministically;
        # the model only needs enough of it to recognise the shape.
        items = [_prune(v, depth + 1) for v in obj[:8]]
        return [i for i in items if i not in (None, "", [], {})]
    if isinstance(obj, str):
        return obj[:600]
    return obj


def build(doc, graph, nodes, fields, image_urls: list[str], options) -> str:
    parts: list[str] = []

    known = {
        "name": fields.name,
        "brand": fields.brand,
        "price": fields.price,
        "currency": fields.currency,
        "breadcrumb": " > ".join(fields.breadcrumb) if fields.breadcrumb else None,
        # Names *and* a sample of values: the model is asked to judge what an axis should be
        # called from what it contains, and an axis called "Item" holding Regular and Tall is
        # only recognisable as a Fit if you can see Regular and Tall.
        "variant_axes": [{"name": o["name"], "values": o["values"][:6]} for o in options],
    }
    parts.append(
        "### already extracted (treat as given; do not restate)\n"
        + json.dumps({k: v for k, v in known.items() if v}, ensure_ascii=False)
    )

    seen_serialised: set[str] = set()
    for rank, (score_value, path, node) in enumerate(nodes[:_MAX_CANDIDATES]):
        pruned = _prune(node)
        if not pruned:
            continue
        blob = json.dumps(pruned, ensure_ascii=False)[:_CANDIDATE_BUDGET]
        if blob[:400] in seen_serialised:
            continue
        seen_serialised.add(blob[:400])
        parts.append(f"### structured data (rank {rank + 1}, score {score_value})\n{blob}")

    metas = []
    for node in doc.tree.css("meta"):
        key = node.attributes.get("property") or node.attributes.get("name")
        content = node.attributes.get("content")
        if not key or not content:
            continue
        if re.search(r"og:|twitter:|description|keywords|price|product|category|brand", key, re.I):
            metas.append(f"{key}: {content[:240]}")
    if metas:
        parts.append("### page metadata\n" + "\n".join(metas)[:_META_BUDGET])

    if image_urls:
        # Filenames only. The model needs to tell gallery shots from a stray logo or a
        # cross-sell thumbnail, and the filename carries that; the full URL is 100 tokens of
        # CDN transform parameters that would tell it nothing extra.
        listing = "\n".join(
            f"[{i}] {re.sub(r'[?#].*$', '', u).rsplit('/', 1)[-1][:70] or u[-70:]}"
            for i, u in enumerate(image_urls[:_IMAGE_LIST])
        )
        parts.append("### candidate images (refer to these by index)\n" + listing)

    parts.append("### visible page text\n" + visible_text(doc))
    return "\n\n".join(parts)
