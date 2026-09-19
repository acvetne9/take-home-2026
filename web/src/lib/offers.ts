import type { CatalogEntry, Offer, Variant } from "./types";
import { effectivePrice } from "./utils";

/**
 * Project a catalog row into the list of offers a shopper can act on.
 *
 * There is exactly one offer per product here, because the input to this system is one product
 * detail page. The *shape* is a list anyway, and that is the point: the unit of a product graph
 * is one canonical product joined to N merchant listings, keyed on the GTIN/MPN that the
 * `Variant` model already carries. Writing this as a scalar `price` + a single link would make
 * the second merchant a rewrite rather than another row.
 *
 * The selected variant wins when it states its own price or availability — a tall fit or a
 * larger size genuinely costs more, and showing the base price beside a chosen variant that
 * costs more is a quietly wrong number.
 */
export function offersFor(entry: CatalogEntry, selected: Variant | null): Offer[] {
  const { product } = entry;
  if (!entry.url) return [];

  return [
    {
      url: entry.url,
      domain: domainOf(entry.url),
      price: effectivePrice(product.price, selected?.price),
      availability: selected?.availability ?? "unknown",
      sku: selected?.sku ?? product.variants[0]?.sku ?? null,
    },
  ];
}

/** `https://www.acehardware.com/departments/...` -> `acehardware.com`. */
export function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url.replace(/^https?:\/\//, "").replace(/^www\./, "").split("/")[0];
  }
}
