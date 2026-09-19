"""Step 8 — map the product onto Google's Product Taxonomy.

The taxonomy has 5,595 leaves. Three approaches and why two of them lose:

* **Send the whole list to the model.** ~120K tokens per product. At 50M products that single
  decision costs more than everything else combined.
* **Pure lexical retrieval.** Free, and confidently wrong across domains: a "cotton lyocell"
  garment retrieves fabric and craft-supply categories, because the words genuinely match
  there. The failure is not noise, it is a plausible wrong branch.
* **Embeddings + vector search** — the right answer where one is available: the provider used
  here serves no embedding models at all, and doing it locally means a multi-gigabyte torch
  dependency.

So: a cheap semantic call narrows 21 roots to 2 (which is what kills the cross-domain
failure), lexical retrieval ranks *within those roots only*, and a second cheap call picks the
leaf from a 20-path shortlist. Twenty, not sixty — a wider shortlist scored *worse* at 1.6x
the cost, because the extra paths are near-misses that compete with the right answer.

The query matters more than the model. An earlier version of this scored 80% on hand-written
queries and 60% on queries built from the page, and the gap was entirely the queries: the
hand-written ones ended in a clause that restated the target leaf. What is measured here is
the realistic number. Two deliberate choices follow from that:

* **Use the breadcrumb.** It is a category path written by the merchant — the most
  on-distribution signal on the page, and free.
* **Retrieve and pick on different inputs.** The lexical stage is dilution-sensitive: dense
  spec prose swamps the TF-IDF vector and the right leaf falls out of the top 20. So it gets a
  short, high-signal query. The model is not dilution-sensitive, so it sees more context.

One property matters more than accuracy: the answer must always validate. A wrong-but-valid
leaf is a data-quality issue to be reviewed; an invalid one is a hard failure that loses the
whole product. Every path we return is copied from the taxonomy file, never generated.
"""

from __future__ import annotations

import collections
import math
import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

TAXONOMY_FILE = Path(__file__).resolve().parent.parent / "categories.txt"
_WORD = re.compile(r"[a-z]+")
_MAX_QUERY = 320
_SHORTLIST = 20
_ROOTS_PICKED = 2
_BRANCHES = 2          # how many likely branches to open up fully
_EXPANDED = 45         # total shortlist size after expansion
_DESC_CHARS = 120      # how much description the *retriever* sees


class _Roots(BaseModel):
    roots: list[str]
    # Query expansion, and the cheapest accuracy we can buy anywhere in the pipeline. Lexical
    # retrieval cannot bridge a vocabulary gap: a page that says "trousers" will never rank
    # "Pants" highly, however good the ranker, because the words simply do not overlap. A model
    # closes that gap for about ten output tokens, on a call we were making anyway.
    category_phrase: str = ""


class _Leaf(BaseModel):
    path: str


def _tokens(text: str) -> list[str]:
    """Words plus character 4-grams.

    The 4-grams are what make singular/plural and compound forms match — "trouser" against
    "Trousers", "drill" inside "Handheld Power Drills" — without a stemmer.
    """
    text = text.lower()
    return _WORD.findall(text) + [text[i:i + 4] for i in range(max(0, len(text) - 3))]


class Taxonomy:
    """TF-IDF over the taxonomy paths. Built once; the vectors are reused for every product."""

    def __init__(self, path: Path = TAXONOMY_FILE):
        self.paths = [
            line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self.roots = sorted({p.split(">")[0].strip() for p in self.paths})
        self._valid = set(self.paths)
        docs = [_tokens(p) for p in self.paths]
        df = collections.Counter(t for d in docs for t in set(d))
        n = len(docs)
        self._idf = {t: math.log(n / (1 + c)) for t, c in df.items()}
        self._default_idf = math.log(n)
        self._vectors = [self._vector(d) for d in docs]
        self._children: dict[str, list[str]] = collections.defaultdict(list)
        for path_str in self.paths:
            parts = [x.strip() for x in path_str.split(">")]
            if len(parts) > 1:
                self._children[" > ".join(parts[:-1])].append(path_str)

    def _vector(self, tokens: list[str]) -> dict[str, float]:
        counts = collections.Counter(tokens)
        vec = {
            t: (1 + math.log(c)) * self._idf.get(t, self._default_idf)
            for t, c in counts.items()
        }
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def shortlist(self, query: str, roots: list[str] | None = None, k: int = _SHORTLIST) -> list[str]:
        keep = {r.strip().lower() for r in roots} if roots else None
        indices = [
            i for i, p in enumerate(self.paths)
            if not keep or p.split(">")[0].strip().lower() in keep
        ] or list(range(len(self.paths)))
        qv = self._vector(_tokens(query))
        scored = sorted(
            ((sum(qv.get(t, 0.0) * w for t, w in self._vectors[i].items()), i) for i in indices),
            reverse=True,
        )[:k]
        return [self.paths[i] for _s, i in scored]

    def expand(self, shortlist: list[str], branches: int = _BRANCHES,
               cap: int = _EXPANDED) -> list[str]:
        """Open up the most likely branches in full, so the answer does not have to be a
        lexical match to be reachable.

        This is the fix for the one failure mode retrieval cannot argue its way out of: the
        page and the taxonomy using different words for the same thing. A page that says
        "trousers" will never rank "Pants" highly, because there is no shared token to rank on
        — and no amount of tuning the ranker changes that.

        What *does* survive the vocabulary gap is the branch. Even a confused shortlist puts
        most of its entries under roughly the right part of the tree, so we let the shortlist
        vote (weighted by rank) for a couple of branches and then list those branches' children
        exhaustively. The right leaf does not need to be retrievable any more; it only needs to
        be a sibling of something retrievable.

        Children are interleaved round-robin rather than appended branch by branch, because the
        first branch is often the wrong one and a wide first branch would otherwise consume the
        whole budget before the second is reached.
        """
        votes: collections.Counter = collections.Counter()
        for rank, path_str in enumerate(shortlist):
            parts = [x.strip() for x in path_str.split(">")]
            for depth in (2, 3):
                if len(parts) >= depth:
                    votes[" > ".join(parts[:depth])] += 1.0 / (rank + 1)

        out = list(shortlist)
        queues = []
        for prefix, _weight in votes.most_common(branches):
            branch = ([prefix] if prefix in self._valid else []) + self._children.get(prefix, [])
            queues.append([p for p in branch if p not in out])
        for i in range(max((len(q) for q in queues), default=0)):
            for q in queues:
                if i < len(q) and len(out) < cap:
                    out.append(q[i])
        return out[:cap]

    def is_valid(self, path: str) -> bool:
        return path in self._valid

    def coerce_with_reason(self, path: str, shortlist: list[str]) -> tuple[str, str]:
        """Force any answer onto a real taxonomy path, and say how hard that was.

        Models paraphrase, drop a level, or re-space the separators even when told to copy
        exactly. Each of those is recoverable, and recovering it beats failing validation.

        The *reason* matters as much as the path. It is the pipeline's signal for whether to
        escalate: an answer that needed only whitespace normalising is a correct answer that
        was formatted differently, and retrying it on a larger model buys nothing. An answer we
        had to guess at by token overlap is a model that stopped following the instruction, and
        that is worth a second opinion.
        """
        candidate = (path or "").strip()
        if self.is_valid(candidate):
            return candidate, "exact"
        squashed = re.sub(r"\s*>\s*", " > ", candidate)
        if self.is_valid(squashed):
            return squashed, "respaced"
        lowered = squashed.lower()
        for p in self.paths:
            if p.lower() == lowered:
                return p, "recased"
        if candidate:
            wanted = set(_WORD.findall(lowered))
            best = max(
                shortlist or self.paths,
                key=lambda p: len(wanted & set(_WORD.findall(p.lower()))),
                default="",
            )
            if best:
                return best, "guessed"
        return (shortlist[0] if shortlist else self.paths[0]), "fallback"

    def coerce(self, path: str, shortlist: list[str]) -> str:
        return self.coerce_with_reason(path, shortlist)[0]


@lru_cache(maxsize=1)
def load() -> Taxonomy:
    return Taxonomy()


# -- query construction -------------------------------------------------------------------

_BREADCRUMB_NOISE = re.compile(
    r"^(?:home|homepage|shop|all|back|index|browse|catalog|sale|new|item\s*#|www\.|https?:)",
    re.I,
)


def retrieval_query(name: str | None, brand: str | None, breadcrumb: list[str],
                    description: str | None) -> str:
    """The short, high-signal string the lexical retriever sees.

    Built from the breadcrumb first, then the name, then only the *first sentence* of the
    description. The cap is not a token-saving measure — retrieval is free — it is an accuracy
    measure: a long spec paragraph dilutes the query vector until the correct leaf drops out of
    the shortlist entirely, and descriptions get longer, not shorter, on real pages.
    """
    parts: list[str] = []
    trail = [
        c for c in breadcrumb
        if c and not _BREADCRUMB_NOISE.match(c.strip()) and len(c) < 50
    ]
    # Drop a trailing crumb that is just the product name — it adds no category signal.
    if trail and name and trail[-1].strip().lower() in name.strip().lower():
        trail = trail[:-1]
    if trail:
        parts.append(" ".join(trail))
    if name:
        parts.append(name)
    if description:
        # First sentence, hard-capped. The cap is an accuracy measure, not a cost one:
        # retrieval is free, but every extra word of spec prose pulls the query vector towards
        # whatever materials and components the copy happens to mention.
        first = re.split(r"(?<=[.!?])\s", description.strip(), maxsplit=1)[0]
        parts.append(first[:_DESC_CHARS])
    query = " ".join(parts)
    # The brand name is anti-signal for a *category*: it is a proper noun that matches nothing
    # in the taxonomy and dilutes everything that does.
    if brand:
        query = re.sub(re.escape(brand), " ", query, flags=re.I)
    return re.sub(r"\s+", " ", query).strip()[:_MAX_QUERY]


def lexical_only(query: str) -> str:
    """The zero-cost answer. Used when no model is available, and as the cascade's floor."""
    tax = load()
    return tax.shortlist(query, k=1)[0]


async def classify_roots(query: str, model: str, ai_module) -> tuple[list[str], str]:
    """Narrow 21 roots to 2 — the step that kills lexical retrieval's cross-domain failures.

    It is the cheapest call in the pipeline (the whole root list is about 200 tokens) and the
    one that buys the most: once the branch is right, lexical ranking inside it is reliable.
    """
    tax = load()
    roots_response = await ai_module.responses(
        model,
        [{"role": "user", "content":
            f"Product: {query}\n\n"
            f"1. roots: which {_ROOTS_PICKED} of the top-level retail categories below is this "
            f"product most likely to belong to? Use the names exactly as written.\n"
            f"2. category_phrase: two to five words naming this kind of product in the most "
            f"standard, generic retail vocabulary - the words a product taxonomy would use "
            f"rather than the words this seller used. No brand names.\n\n"
            + "\n".join(f"- {r}" for r in tax.roots)}],
        text_format=_Roots,
        reasoning={"effort": "minimal"},
    )
    return ([r for r in (roots_response.roots or []) if r],
            (roots_response.category_phrase or "").strip()[:60])
