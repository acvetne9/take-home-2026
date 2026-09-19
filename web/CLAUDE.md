# `web/`

Vite + React + TypeScript + Tailwind v4. shadcn-idiom primitives kept in-tree
(`components/ui/primitives.tsx`) rather than installed, so the design system is ours and
legible in one file.

**Two pages:** `pages/Catalog.tsx` (grid) and `pages/ProductDetail.tsx` (PDP). Data comes from
the extracted catalog via `lib/api.ts`. No database, no persistence.

## Scope

The storefront is the catalog and the detail view. No auth, no home/about pages, no search bar,
no cart, no ratings, no price history, no recommendations — none of that is backed by anything
the extractor produces, and a UI element with no record behind it is a fabricated one.

## Conventions

| convention | where |
|---|---|
| offers sorted ascending; cheapest **purchasable** one badged; out-of-stock dimmed under its own label; `rel="sponsored noopener noreferrer"` outbound | `OfferList.tsx`, `lib/offers.ts` |
| a `<dl>` that drops empty rows and renders nothing when none survive | `Specs.tsx` |
| `Intl.NumberFormat` with a `CODE 12.34` fallback | `lib/utils.ts` |
| cards crossfade to a contextual second shot on hover | `ProductImage.tsx` |
| contained images for grids, the regular shot for detail galleries | `object-contain` tiles vs `Gallery.tsx` |

## Let the data model show

- **Render the merchant link as an offer row, not a button.** One product, many merchants: the
  offer list is the graph join, and it is the PDP's primary CTA.
- **The variant picker is where the backend schema becomes visible.** Publishing axes and
  concrete SKUs separately is what lets a chosen colour grey out the sizes it isn't made in.
  See `lib/variants.ts`, `VariantPicker.tsx`.

## Rules

- **Omit, never pad.** The root's refusal stance applied to rendering: colour swatches stay
  silent rather than guess a name, empty sections are dropped rather than filled from the
  description, `Specs` returns null rather than a dash.
- **Filter images only on a signal already in the data**, never a URL keyword list — that shape
  is the site-specific-logic invariant. No general signal means do nothing.
- **Format currency in the currency the record states.** One sample page prices in GBP;
  rendering that as dollars is a quietly wrong number.
- **Lock aspect ratios.** Fixed aspect + `object-contain` on a neutral ground, so a lifestyle
  shot and a white-background tile sit in the same row and nothing reflows as images land.
- **Degrade honestly.** `ProductImage`'s `decorative` mode keeps a failed decorative layer from
  painting an error message. Filter chips collapse to a `<select>` past eight values.
- **Keep the storefront framing.** The provenance drawer under "Extraction details" is the
  right amount of inspector; an extraction dashboard is not what this is.
- Focus rings, alt text, arrow-key gallery navigation, empty/error/404 states, dark mode. The
  card → PDP View Transition is guarded by `prefers-reduced-motion`.

## Verify in a browser

`tsc` and lint cannot see a hover state, a reflow, or a filter row that only breaks past n=8.
Drive the real pages before calling a change done.
