"""Step 9b — the one structured model call, and what it is allowed to return.

The rule that governs this file: **never ask the model for anything Python can derive.**

Not because the model would get it wrong, but because of where the cost and the failure modes
live. Output tokens are ~8x input tokens on the small models, so re-emitting a 25-URL gallery
is the single most expensive thing the pipeline could do; and long structured output is exactly
where models truncate mid-string and invent plausible path segments. A URL that is wrong in one
character is indistinguishable from a correct one until someone fetches it.

So the model answers five questions, all of them short and all of them genuine judgement:

  * which taxonomy line, copied from a 20-line shortlist;
  * which colours this product comes in, as names a person would use;
  * what the key features are, in the page's own words;
  * what the variant axes should be *called* ("Item" with values Regular/Tall is a Fit);
  * which of the candidate images show this product, **by index**.

Target output: under 200 tokens.

`reasoning={"effort": "minimal"}` on every call. It is 7-9x cheaper *and* measurably better
here — with default reasoning the model spent its budget re-deriving facts the bundle had
already given it, and returned emptier answers.
"""

from __future__ import annotations

import logging
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# How much of the resolved gallery the model's pruning must retain to be believed at all.
_MIN_IMAGE_RETENTION = 0.75

# ---------------------------------------------------------------------------------------
# Not every model failure means the model was wrong.
#
# The cascade escalates to a larger, dearer model when an answer is defective. A rate limit or
# a gateway timeout is not a defective answer — it is no answer — and treating the two alike is
# expensive in the one direction that matters: a provider incident would silently promote every
# product to a model that costs 4x, exactly when the provider is already struggling.
#
# So transient failures retry the *same* model, and only genuine defects escalate.
# ---------------------------------------------------------------------------------------
_TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_TRANSIENT_NAMES = ("ratelimit", "timeout", "apiconnection", "internalserver",
                    "serviceunavailable", "overloaded")


def is_transient(exc: BaseException) -> bool:
    """True when retrying the same model is the right move.

    Duck-typed rather than importing the provider SDK's exception tree: `ai.py` is off limits
    and the wrapper is meant to be swappable, so this must not depend on which client is behind
    it. An HTTP status is checked first because it is unambiguous; the class name is the
    fallback for transport errors that carry no status at all.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status in _TRANSIENT_STATUS
    name = type(exc).__name__.lower()
    return any(marker in name for marker in _TRANSIENT_NAMES)


def retry_after(exc: BaseException) -> float | None:
    """Seconds the provider asked us to wait, when it said so. A 429 with `Retry-After` is the
    one case where the right delay is known rather than guessed."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


class AxisName(BaseModel):
    """A rename for one variant axis. A list of pairs, not a map: the structured-output schema
    forbids open-ended objects, so an axis map has to be expressed as pairs and folded after
    parsing."""
    original: str
    normalized: str


class Reconciliation(BaseModel):
    category_path: str = Field(description="One line copied exactly from the category list")
    key_features: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    axis_names: list[AxisName] = Field(default_factory=list)
    product_image_indices: list[int] = Field(default_factory=list)


_PROMPT = """You are extracting one product from a product detail page. Everything below was
read from the page itself; facts under "already extracted" are settled, do not restate them.

Return exactly these fields:

- category_path: copy ONE line from the category list at the end, character for character,
  including every ">" separator. Do not invent, abbreviate or re-word a path. Choose the
  category for the product ITSELF, not for an accessory, spare part, or something it is used
  with - a lamp belongs under lamps, not under lamp shades or lamp parts. Prefer the most
  specific line that is still true of the whole product.
- key_features: up to 6 short factual bullets about the product, taken from the page. Each
  under 12 words. No marketing sentences, no repetition of the title. Empty list if the page
  does not state any.
- colors: the colours this product is available in, as a shopper would name them. Empty list
  if the product does not come in colours.
- axis_names: for each variant axis under "variant_axes", give the standard shopper-facing
  name for it, judged from its values. ONE or TWO words - the kind of label a size-and-colour
  picker uses (Size, Color, Fit, Length, Width, Material, Style, Capacity). Never a slash, a
  sentence, or the word "variant". If the existing name is already one of those, repeat it
  unchanged.
- product_image_indices: the indices of the candidate images that show THIS product. Include
  every gallery photograph of it, including alternate angles, close-ups and each colourway.
  Exclude only what clearly is not a photograph of this product: logos, size charts, banners,
  and photographs of a different product. If the filenames are opaque ids that tell you
  nothing, assume they are all gallery photographs and return every index.

Never guess. An empty list is a correct answer when the page does not say.

--- PAGE EVIDENCE ---
{bundle}

--- CATEGORY LIST (copy one line exactly) ---
{categories}
"""


async def reconcile(bundle: str, shortlist: list[str], model: str, ai_module) -> Reconciliation | None:
    """The single judgement call. Returns `None` if the model call fails, so the pipeline can
    fall back to its deterministic answers rather than losing the product."""
    prompt = _PROMPT.format(bundle=bundle, categories="\n".join(f"- {p}" for p in shortlist))
    try:
        return await ai_module.responses(
            model,
            [{"role": "user", "content": prompt}],
            text_format=Reconciliation,
            reasoning={"effort": "minimal"},
        )
    except Exception as exc:
        if is_transient(exc):
            # Let the caller decide: it can retry this same model. Returning None here would
            # read as "the model answered badly" and trigger an escalation we did not intend.
            raise
        logger.warning("reconcile call failed (%s); falling back to deterministic fields", exc)
        return None


def apply_image_judgement(image_urls: list[str], indices: list[int], window: int) -> list[str]:
    """Let the model *prune* the gallery, never rebuild it.

    This is a precision backstop, not a selection step, and the distinction is load-bearing.
    The model judges an image from its filename, and plenty of CDNs emit opaque ids, so on
    those pages its "judgement" is a guess. We therefore accept the pruning only when it
    removes a small minority — the shape of "there is a logo in here", which is what it is for.
    A judgement that discards a quarter of the gallery is not spotting outliers; it is
    re-selecting, on worse evidence than the resolver had, and we decline it.

    The model can only ever remove, so it cannot introduce a URL that was not on the page.
    """
    if not indices or not image_urls:
        return image_urls
    judged, unseen = image_urls[:window], image_urls[window:]
    keep = [judged[i] for i in sorted(set(indices)) if 0 <= i < len(judged)]
    if len(keep) < max(1, int(len(judged) * _MIN_IMAGE_RETENTION)):
        logger.info("ignoring image judgement: kept %d of %d", len(keep), len(judged))
        return image_urls
    # Anything past the indexed window was never shown to the model, so it cannot have judged
    # it. Keeping it is the honest default; dropping it would silently truncate a long gallery.
    return keep + unseen
