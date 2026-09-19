# `extract/`

Deterministic stages first, then one structured model call.

```
dom → markup → harvest → graph → fields ─┬─ images ─┬─ bundle → taxonomy + reconcile → pipeline
                                          ├─ media   │
                                          └─ variants┘
```

Order is load-bearing: by the time a model is involved the pipeline already has name, brand,
price, images, video and the full variant matrix, so `pipeline.py` can decide **whether to ask
at all**. Refusal guards run before any spend.

| module | job |
|---|---|
| `dom.py` | decode once, parse once; strict decoding; one shared tree |
| `markup.py` | Microdata + RDFa → the same dict shape as JSON-LD; breadcrumbs |
| `harvest.py` | every JSON object in the page, from eight carriers |
| `graph.py` | reference resolution over normalised caches; node scoring |
| `fields.py` | per-field priority chains: name, brand, description, price |
| `images.py` | the gallery, at max resolution |
| `media.py` | the product video, or an honest `None` |
| `variants.py` | axes and SKUs, derived in Python |
| `taxonomy.py` | retrieval + branch expansion over the 5,595-leaf taxonomy |
| `bundle.py` | the whole page → an ordered evidence bundle, under budget |
| `reconcile.py` | the one structured call, and what it may return |
| `pipeline.py` | orchestration, confidence floor, escalation cascade |

**Why each module is shaped the way it is: read its docstring.** They are maintained with the
code; this file is only the part that spans modules.

## Rules

**Model scope**
- Never ask the model for anything Python can derive. It returns a category line copied from a
  shortlist, feature bullets, colour names, axis renames, and image **indices** — no URL, no
  variant row, no price. Output tokens cost ~8× input, and long structured output is where
  models truncate and confabulate.
- Target under 200 output tokens.
- `reasoning={"effort": "minimal"}` on every call. Fixed-schema extraction is transcription,
  not deliberation.
- Image judgement is a precision backstop, not a selection step: accept the model's pruning
  only when it removes a small minority. It may remove, never introduce.

**Ownership — precision comes from here, not from reading less**
- Breadth across carriers is close to free; more structure lets the selector find a *tighter*
  product node.
- A Microdata `itemprop` binds to its **nearest ancestor `itemscope`**. Tree operation, not
  regex — this is why there is a parser.
- **A member node never outranks its subject.** Nodes under `hasVariant`, `itemListElement`
  or `related` are real product nodes, just not this page's.
- Scope money to the DOM subtree around the product title; exclude merchandising vocabulary.

**Reading**
- A placeholder is not null. Reject by DOM context and sentinel string, not by emptiness — on a
  modern page the alternative to a real value is a skeleton, not an absent one.
- Match attribute names case-insensitively. HTML attribute names are ASCII case-insensitive and
  JSX frameworks emit their source spelling, so real pages ship camelCase `itemProp`.
- Test the *shape* of a value, not a naming fashion. "Any attribute whose value parses as JSON"
  carries no site knowledge; `data-product-json` does.
- Drop what you cannot resolve; never guess it. The JavaScript-object reader is a reader, not
  an evaluator. Dropping loses data; guessing invents it.

**Publishing**
- Refusal guards are categorical, never confidence multipliers. A guard that arithmetic can
  outvote is not a guard.
- Never coerce a missing price to `0.0`. `UnpriceablePage` exists so the refusal is not
  optional.
- Decide the decimal mark **positionally**, from where the separators fall — never from a
  domain, language tag or locale.
- Demote range endpoints (`lowPrice`) and out-of-stock offers; exclude charge subtrees
  (shipping, tax) by schema.org type.
- Category: validity before accuracy. Every path is *copied* from the taxonomy file, never
  generated — a wrong-but-valid leaf is reviewable, an invalid one fails validation and loses
  the whole product.

**Escalation**
- "No answer" is not "bad answer". `reconcile.is_transient()` separates them: transient
  provider failures retry the same model, only defects escalate.
- A defect is concrete: a category we had to *guess* at, an empty feature list on a text-rich
  page, no image indices on a multi-image gallery. Formatting differences are not defects — an
  answer that only needed re-spacing is correct.

## Adding a carrier

1. Name the published convention it implements.
2. Test the shape of the value, not the name.
3. Match case-insensitively.
4. Run the leave-one-out check. A constant load-bearing for exactly one site *while several
   sites exercise that path* is the overfit smell; a constant that changes nothing across every
   page is dead weight.

## Scope boundaries

The pipeline is handed raw HTML and returns records. It contains no fetching machinery —
network code never lives here — and it never renders. Policy on a non-200 response is **never
render**: a headless pass manufactures plausible DOM out of a block page, converting an honest
refusal into a silent error.

Variants are read from published structure, not from `data-*` attributes on buttons, whose
names differ per site. The JavaScript-object reader resolves literals; payloads that need a JS
engine are dropped rather than guessed at.

The variant schema is a flat cross-product of axes, so dependent options — a Size whose choices
depend on Material — are expressed as concrete SKUs rather than as a conditional axis.
