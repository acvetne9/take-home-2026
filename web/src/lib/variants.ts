import type { Variant, VariantOption } from "./types";

/** The single variant matching a full selection, or null while the selection is incomplete. */
export function matchVariant(
  variants: Variant[],
  options: VariantOption[],
  selection: Record<string, string>,
): Variant | null {
  if (options.length === 0) return variants[0] ?? null;
  if (Object.keys(selection).length !== options.length) return null;
  return (
    variants.find((v) =>
      Object.entries(selection).every(([name, value]) => v.options[name] === value),
    ) ?? null
  );
}
