"""Step 6 — the product video.

This looks like a one-liner and is not. Neither obvious rule works on real pages:

* "Find the `<video>` tag" misses pages whose player is mounted by JavaScript — the element is
  present but hidden and carries no `src` until it is initialised.
* "Find an mp4" overshoots badly: a PDP routinely carries brand films, a size-guide animation
  and several editorial clips, so a page can offer five candidates for a `str | None` field.

Video is the image problem with n=1, so it is solved the same way: collect from the
dereferenced graph and the markup, test the media type, then scope to the product through
progressively weaker ownership claims. A clip inside the product's own media container is the
product's video; a page-level brand film is not, and we prefer `None` over a plausible wrong
answer for a field the page never claims belongs to this product.
"""

from __future__ import annotations

import re

from .dom import Document, context_words
from .graph import walk
from .images import EXCLUDE, _identity_key, _is_img_url, _norm, identity_tokens

VIDEO_EXT = re.compile(r"\.(?:mp4|m4v|webm|mov|ogv|m3u8|mpd)(?:$|[?#])", re.I)
# Keys that conventionally hold a video URL.
VIDEO_KEYS = {
    "videourl", "video_url", "video", "contenturl", "content_url", "embedurl", "embed_url",
    "src", "url", "file", "source", "playbackurl", "hlsurl", "mp4",
}
# A sibling field that says "this media item is a video" even when the URL does not.
VIDEO_TYPE = re.compile(r"\bvideo\b", re.I)
_VIDEO_TYPE_KEYS = ("mediatype", "type", "@type", "component", "cardtype", "kind", "format", "assettype")
_MAX_URL = 2000


def _is_video_url(value) -> bool:
    if not isinstance(value, str) or not (8 < len(value) < _MAX_URL):
        return False
    if value.startswith("data:"):
        return False
    if not (value.startswith("http") or value.startswith("//")):
        return False
    return bool(VIDEO_EXT.search(value))


def _declares_video(node: dict) -> bool:
    for key in _VIDEO_TYPE_KEYS:
        for k, v in node.items():
            if str(k).lower() == key and isinstance(v, str) and VIDEO_TYPE.search(v):
                return True
    return False


def _from_graph(graph) -> list[tuple[str, str]]:
    """`(path, url)` for every video-shaped value in the graph."""
    out = []
    for i, blob in enumerate(graph):
        for path, node in walk(blob):
            declared = _declares_video(node)
            for k, v in node.items():
                if not isinstance(v, str):
                    continue
                key = str(k).lower()
                if key not in VIDEO_KEYS:
                    continue
                if _is_video_url(v) or (declared and v.startswith(("http", "//")) and not _is_img_url(v)):
                    out.append((f"b{i}.{path}.{k}" if path else f"b{i}.{k}", _norm(v)))
    return out


def _from_markup(doc: Document) -> list[tuple[str, str]]:
    base = doc.canonical_url()
    out = []
    for node in doc.tree.css("video, source"):
        if EXCLUDE.search(context_words(node, limit=8)):
            continue
        attrs = node.attributes
        mime = (attrs.get("type") or "").lower()
        for attr in ("src", "data-src", "data-video-src"):
            raw = attrs.get(attr)
            if not raw:
                continue
            url = _norm(raw, base)
            if _is_video_url(url) or mime.startswith("video/"):
                out.append((f"markup.{node.tag}.{attr}", url))
    for key in ("video", "video:url", "video:secure_url"):
        value = doc.meta(key)
        if value and _is_video_url(_norm(value, base)):
            out.append(("markup.og", _norm(value, base)))
    return out


def resolve_video(doc: Document, graph) -> str | None:
    """The one video that belongs to this product, or `None`."""
    all_nodes = [(p, n) for blob in graph for p, n in walk(blob) if isinstance(n, dict)]
    url_tokens, key_tokens = identity_tokens(doc, all_nodes)

    candidates = _from_graph(graph) + _from_markup(doc)
    candidates = [c for c in candidates if not EXCLUDE.search(c[0]) and not EXCLUDE.search(c[1])]
    if not candidates:
        return None

    # Same ownership ladder as the images, strongest claim first.
    tiers = [
        [c for c in candidates if any(t in c[1] for t in url_tokens)],
        [c for c in candidates if set(re.split(r"[.\[\]]+", c[0])) & set(key_tokens)],
        [c for c in candidates if c[0].startswith("markup")],
    ]
    for tier in tiers:
        if tier:
            candidates = tier
            break

    # Several distinct clips with no tie-break means we cannot say which is "the" product video.
    # Claiming one would be a guess dressed as an extraction, so we decline.
    distinct = {_identity_key(u) for _p, u in candidates}
    if len(distinct) > 1 and not any(any(t in u for t in url_tokens) for _p, u in candidates):
        return None
    return candidates[0][1]
