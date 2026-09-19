import type { Product, Variant } from "@/lib/types";

/**
 * The spec sheet: every identifying fact the page actually stated, as scannable label/value
 * rows rather than prose.
 *
 * Two rules, both of which are the whole reason this reads as a spec sheet and not as a form:
 * a row is rendered only when its value exists — never a placeholder dash, which makes a table
 * look broken rather than sparse — and the whole section disappears when nothing survives.
 *
 * Identifiers (SKU, GTIN, MPN) follow the *selected* variant, because that is what identifies
 * the thing you would actually receive. GTIN in particular is the join key that lets the same
 * physical item be recognised across retailers, so it belongs on the page rather than only in
 * the payload.
 */
export function Specs({ product, selected }: { product: Product; selected: Variant | null }) {
  const identity = selected ?? (product.variants.length === 1 ? product.variants[0] : null);

  const rows: Array<[string, string]> = [];
  const push = (label: string, value: string | null | undefined) => {
    if (value && value.trim()) rows.push([label, value.trim()]);
  };

  push("Brand", product.brand);
  push("Category", product.category.name);
  // Each chosen axis is a fact about the configuration, so it belongs here as well as in the
  // picker: "Color: Navy Blue" is a spec once you have selected it.
  for (const [axis, value] of Object.entries(selected?.options ?? {})) push(axis, value);
  if (!selected && product.colors.length) {
    push(product.colors.length > 1 ? "Colors" : "Color", product.colors.join(", "));
  }
  push("SKU", identity?.sku);
  push("GTIN", identity?.gtin);
  push("MPN", identity?.mpn);

  if (!rows.length) return null;

  return (
    <section className="mt-7 border-t border-line pt-7">
      <h2 className="mb-3 text-sm font-medium">Specifications</h2>
      <dl className="grid grid-cols-[minmax(6rem,auto)_1fr] gap-x-6 gap-y-0 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="border-b border-line/70 py-2 text-muted-fg">{label}</dt>
            <dd className="border-b border-line/70 py-2 break-words">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
