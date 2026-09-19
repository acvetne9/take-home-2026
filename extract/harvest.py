"""Step 2b — find every machine-readable fact the page already states.

The thesis of this whole extractor is that a product page is not prose to be comprehended; it
is a document that has *already been rendered from a database*, and most of the database is
still in the file. The job is to read it, not to ask a model to re-read it in English.

So the harvester is deliberately broad about *carriers* and deliberately dumb about *meaning*.
It returns every JSON object it can find, from six different places, and lets later stages
decide what any of it means:

1. `<script type="application/ld+json">` — schema.org, the case everybody handles.
2. **Any `<script>` whose body parses as JSON**, whatever its `type`. The `type` attribute is
   advisory; gating on it makes several major commerce platforms invisible for no reason.
3. `window.__STATE__ = {...}` style assignments — SSR framework hydration state. Brace-matched
   rather than regex-captured, and we scan *every* assignment in a script, not the first: one
   sampled 445 KB script assigns two different state objects back to back, and the product data
   was in the second.
4. `JSON.parse("...")` string arguments — how several frameworks embed their payload.
5. **JSON in any attribute.** Island/hydration frameworks put component props in an HTML
   attribute as escaped JSON. We test the *shape* of the value rather than a naming fashion,
   so this is not restricted to `data-*`.
6. Microdata and RDFa, via `markup.py`, normalised into the same dict shape.

Breadth here is close to free: measured across the sample pages, adding carriers 2-6 changed
the evidence bundle by an average of *minus* 75 tokens, because the extra structure lets the
selector find a tighter product node instead of shipping a large one.

Nothing in this module names a site, a domain, or a framework-specific key.
"""

from __future__ import annotations

import html as html_mod
import json
import re

from .markup import microdata, rdfa

# `window.__STATE__ = {`, `Static.SQUARESPACE_CONTEXT = {`, `var meta = {`, `app.data = [`.
#
# Restricting this to `window.`/`globalThis.`/`self.` was a guess about where platforms hang
# their state, and it is wrong often enough to matter: Squarespace uses its own `Static.`
# namespace and many themes assign to a plain `var`. The general shape is "an identifier path,
# then `=`, then a literal", which carries no site knowledge at all. Single-character names are
# skipped because minified bundles are full of `a={}` and none of it is product data.
#
# The `(?=[{\[])` lookahead leaves the cursor on the opening bracket so we can brace-match.
_ASSIGNMENT = re.compile(
    r"""(?:^|[;{}()\s,])((?:[A-Za-z_$][\w$]*)(?:\s*\.\s*[A-Za-z_$][\w$]*)*)\s*=\s*(?=[{\[(])"""
)
_MIN_NAME = 2
# `JSON.parse("{\"a\":1}")` — the payload is a JS string literal containing JSON.
_JSON_PARSE = re.compile(r"""JSON\s*\.\s*parse\s*\(\s*(["'])""")
# Any attribute with a longish value, in *either* quoting style. HTML permits both, and
# Salesforce B2C Commerce in particular ships `data-analytics='{"id":…}'` — single-quoted
# precisely so the JSON's own double quotes need no escaping. Double-quote-only matching finds
# nothing on those pages.
_ATTRIBUTE = re.compile(
    r"""\s([a-zA-Z_:][\w:.\-]*)\s*=\s*(?:"([^"]{16,})"|'([^']{16,})')"""
)
# A long string literal inside a script. Server-rendered React (the App Router's flight
# payload) ships its data as escaped JSON *inside* string literals appended to an array —
# `self.__next_f.push([1,"4:[\"$\",\"div\",…]"])` — so the payload is not reachable by
# brace-matching the script body, only by first reading the literal back out.
_LONG_STRING = re.compile(r'"((?:[^"\\]|\\.){120,}?)"', re.S)
_MAX_LITERALS = 400

_MAX_SCAN = 4_000_000
_MIN_BLOB_KEYS = 1


def _match_bracket(text: str, start: int) -> str | None:
    """Return the balanced `{...}` / `[...]` beginning at `start`, string-aware.

    A regex cannot do this: JSON payloads contain braces inside strings, and a non-greedy match
    stops at the first one.
    """
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    limit = min(len(text), start + _MAX_SCAN)
    for i in range(start, limit):
        c = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _js_string(text: str, start: int, quote: str) -> str | None:
    """Read a JS string literal starting just after its opening quote."""
    out: list[str] = []
    escaped = False
    limit = min(len(text), start + _MAX_SCAN)
    for i in range(start, limit):
        c = text[i]
        if escaped:
            out.append(c if c not in "ntr" else {"n": "\n", "t": "\t", "r": "\r"}[c])
            escaped = False
            continue
        if c == "\\":
            escaped = True
            continue
        if c == quote:
            return "".join(out)
        out.append(c)
    return None


def _loads(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


# ---------------------------------------------------------------------------------------
# JavaScript object literals
#
# Not every payload in a page is JSON. A large minority is a JS *object literal*: unquoted
# keys, single-quoted strings, trailing commas. `json.loads` rejects all three, which is why a
# Nuxt page, a PrestaShop config block and a great many hand-rolled theme scripts read as zero
# blobs even though the product is sitting right there.
#
# This is a reader, not an evaluator. Where a value is an expression we cannot resolve — a bare
# identifier, a function call — the key is *dropped* rather than guessed at. Dropping loses
# data; guessing invents it, and invented data is the failure mode this whole module exists to
# avoid.
# ---------------------------------------------------------------------------------------

_UNRESOLVED = object()
_JS_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_JS_NUMBER = re.compile(r"-?(?:0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+)")
_JS_LITERALS = {"true": True, "false": False, "null": None, "undefined": None, "NaN": None}
_JS_MAX_DEPTH = 24


def _js_skip(text: str, i: int) -> int:
    """Advance past whitespace and both comment styles."""
    while i < len(text):
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = len(text) if j < 0 else j + 1
        elif text.startswith("/*", i):
            j = text.find("*/", i)
            i = len(text) if j < 0 else j + 2
        else:
            break
    return i


def _js_value(text: str, i: int, depth: int = 0):
    """Read one JS value at `i`. Returns `(value, next_index)` or `(_UNRESOLVED, i)`."""
    i = _js_skip(text, i)
    if i >= len(text) or depth > _JS_MAX_DEPTH:
        return _UNRESOLVED, i
    c = text[i]

    if c in "\"'`":
        literal = _js_string(text, i + 1, c)
        if literal is None:
            return _UNRESOLVED, i
        # +1 opening quote, +body, +1 closing quote.
        return literal, i + 2 + _js_string_len(text, i + 1, c)

    if c == "{":
        obj, i = {}, i + 1
        while True:
            i = _js_skip(text, i)
            if i >= len(text):
                return _UNRESOLVED, i
            if text[i] == "}":
                return obj, i + 1
            if text[i] == ",":
                i += 1
                continue
            if text[i] in "\"'":
                quote = text[i]
                key = _js_string(text, i + 1, quote)
                if key is None:
                    return _UNRESOLVED, i
                i += 2 + _js_string_len(text, i + 1, quote)
            else:
                m = _JS_IDENT.match(text, i) or _JS_NUMBER.match(text, i)
                if not m:
                    return _UNRESOLVED, i
                key, i = m.group(0), m.end()
            i = _js_skip(text, i)
            if i >= len(text) or text[i] != ":":
                return _UNRESOLVED, i
            value, i = _js_value(text, i + 1, depth + 1)
            if value is _UNRESOLVED:
                i = _js_skip_expression(text, i)
            else:
                obj[key] = value

    if c == "[":
        arr, i = [], i + 1
        while True:
            i = _js_skip(text, i)
            if i >= len(text):
                return _UNRESOLVED, i
            if text[i] == "]":
                return arr, i + 1
            if text[i] == ",":
                i += 1
                continue
            value, i = _js_value(text, i, depth + 1)
            if value is _UNRESOLVED:
                i = _js_skip_expression(text, i)
                arr.append(None)
            else:
                arr.append(value)

    m = _JS_NUMBER.match(text, i)
    if m:
        try:
            raw = m.group(0)
            return (int(raw, 16) if raw[:2].lower() in ("0x", "-0") and "x" in raw.lower()
                    else float(raw) if ("." in raw or "e" in raw.lower()) else int(raw)), m.end()
        except ValueError:
            return _UNRESOLVED, m.end()

    m = _JS_IDENT.match(text, i)
    if m and m.group(0) in _JS_LITERALS:
        return _JS_LITERALS[m.group(0)], m.end()
    return _UNRESOLVED, i


def _js_skip_expression(text: str, i: int) -> int:
    """Skip an expression we will not evaluate, stopping at this level's `,` `}` or `]`."""
    depth = 0
    while i < len(text):
        c = text[i]
        if c in "\"'`":
            i += 2 + _js_string_len(text, i + 1, c)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return i
            depth -= 1
        elif c == "," and depth == 0:
            return i
        i += 1
    return i


def _js_string_len(text: str, start: int, quote: str) -> int:
    """Characters consumed by the string literal body starting at `start`, excluding quotes."""
    escaped = False
    for i in range(start, min(len(text), start + _MAX_SCAN)):
        if escaped:
            escaped = False
        elif text[i] == "\\":
            escaped = True
        elif text[i] == quote:
            return i - start
    return 0


def _loads_js(text: str):
    """JSON first; a tolerant JS object-literal read as a fallback."""
    parsed = _loads(text)
    if parsed is not None:
        return parsed
    try:
        value, _ = _js_value(text, 0)
    except RecursionError:
        return None
    return None if value is _UNRESOLVED else value


def _useful(value) -> bool:
    """Drop the empties early so later stages score fewer nodes."""
    if isinstance(value, dict):
        return len(value) >= _MIN_BLOB_KEYS
    if isinstance(value, list):
        return any(isinstance(v, (dict, list)) for v in value)
    return False


def _script_blobs(doc) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for node in doc.tree.css("script"):
        body = node.text(deep=True) or ""
        if not body.strip():
            continue
        stripped = body.strip()
        script_type = (node.attributes.get("type") or "").lower()

        # (1)+(2) the whole body is JSON — regardless of what `type` claims.
        if stripped[0] in "{[":
            parsed = _loads(stripped)
            if parsed is not None and _useful(parsed):
                label = "ld+json" if "ld+json" in script_type else "script-json"
                out.append((label, parsed))
                continue

        # (3) hydration-state assignments. Every one in the script, not just the first.
        for m in _ASSIGNMENT.finditer(body):
            name = re.sub(r"\s+", "", m.group(1))
            if len(name) < _MIN_NAME:
                continue
            start = _payload_start(body, m.end())
            if start is None:
                continue
            chunk = _match_bracket(body, start)
            if not chunk:
                continue
            parsed = _loads_js(chunk)
            if parsed is not None and _useful(parsed):
                out.append((f"state:{name}", parsed))

        # (4) JSON smuggled through a string literal.
        for m in _JSON_PARSE.finditer(body):
            literal = _js_string(body, m.end(), m.group(1))
            if not literal or literal.lstrip()[:1] not in "{[":
                continue
            parsed = _loads(literal)
            if parsed is not None and _useful(parsed):
                out.append(("json-parse", parsed))

        # (6) JSON embedded in an arbitrary long string literal. Generalises (4): rather than
        # requiring the literal to sit inside a `JSON.parse(...)` call, read any long literal
        # back out and look for a bracket inside it. This is what makes React Server Component
        # payloads legible, and it costs nothing on pages that have none.
        for m in list(_LONG_STRING.finditer(body))[:_MAX_LITERALS]:
            for parsed in _blobs_in_literal(m.group(1)):
                out.append(("script-literal", parsed))
    return out


def _blobs_in_literal(raw: str) -> list[object]:
    """Whatever parses as JSON inside one string literal, once it is unescaped.

    A flight payload is prefixed (`4:[...]`) rather than being bare JSON, so we look for the
    first bracket rather than requiring the literal to start with one.
    """
    if "\\" not in raw:
        return []
    text = _loads(f'"{raw}"')
    if not isinstance(text, str):
        return []
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        return []
    chunk = _match_bracket(text, start)
    if not chunk:
        return []
    parsed = _loads(chunk)
    return [parsed] if parsed is not None and _useful(parsed) else []


def _payload_start(text: str, i: int) -> int | None:
    """Index of the literal a state assignment actually carries.

    Usually that is the character right after `=`. Nuxt and several SSR frameworks instead wrap
    the payload in an immediately-invoked function and hand it back from a `return`, so when we
    land on `(` we look for that `return` rather than giving up. Anything else after `(` is a
    call we cannot evaluate, and we leave it alone.
    """
    i = _js_skip(text, i)
    if i >= len(text):
        return None
    if text[i] in "{[":
        return i
    if text[i] != "(":
        return None
    j = text.find("return", i, i + _MAX_SCAN)
    if j < 0:
        return None
    j = _js_skip(text, j + len("return"))
    return j if j < len(text) and text[j] in "{[" else None


def _attribute_blobs(doc) -> list[tuple[str, object]]:
    """(5) Component props serialised into an HTML attribute.

    Read from the raw markup rather than the tree because the tree has already un-escaped
    entities, and we want to handle both the escaped and unescaped spellings identically.
    """
    out: list[tuple[str, object]] = []
    for m in _ATTRIBUTE.finditer(doc.html):
        raw = m.group(2) if m.group(2) is not None else m.group(3)
        if "&quot;" not in raw and raw.lstrip()[:1] not in "{[":
            continue
        value = html_mod.unescape(raw).strip()
        if value[:1] not in "{[":
            continue
        parsed = _loads(value)
        if parsed is not None and _useful(parsed):
            out.append((f"attr:{m.group(1).lower()}", parsed))
    return out


def harvest(doc) -> list[tuple[str, object]]:
    """Every structured blob in the document, de-duplicated, tagged with its carrier.

    The carrier tag is not decoration: later stages weigh a `schema.org` annotation differently
    from a lump of framework state, and the image resolver needs to know whether a candidate
    came from markup or from JSON.
    """
    blobs = _script_blobs(doc) + _attribute_blobs(doc) + microdata(doc) + rdfa(doc)
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, object]] = []
    for carrier, blob in blobs:
        try:
            key = (carrier, json.dumps(blob, sort_keys=True, default=str)[:800])
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append((carrier, blob))
    return out
