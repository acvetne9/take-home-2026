import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { Price } from "./types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Format using the currency the page actually stated. One of the sample pages prices in GBP
 * because the crawler reached it from a UK edge — rendering that as "$76.99" would be a
 * quietly wrong number, so the currency travels with the price all the way to the screen.
 */
export function formatPrice(value: number, currency: string) {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: Number.isInteger(value) ? 0 : 2,
    }).format(value);
  } catch {
    return `${currency} ${value.toFixed(2)}`;
  }
}

export function discountPercent(price: Price) {
  if (!price.compare_at_price || price.compare_at_price <= price.price) return null;
  return Math.round((1 - price.price / price.compare_at_price) * 100);
}

/** The taxonomy path as breadcrumb segments. */
export function categorySegments(path: string) {
  return path.split(">").map((s) => s.trim()).filter(Boolean);
}

/**
 * A best-effort CSS colour for a swatch dot.
 *
 * Colour names on real PDPs are marketing copy, often compound: "Phantom/College Grey/Team
 * Red/Black". We try each token and take the first that names a real colour, so a compound
 * name still produces a swatch, and return null rather than guessing when none of them do.
 */
export function swatchColor(name: string): string | null {
  const probe = document.createElement("span");
  const tokens = name.split(/[\/,+&]|\s-\s/).map((t) => t.trim()).filter(Boolean);
  // Most specific first: "Navy Blue" is a colour, and so is "Navy", but trying single words
  // first would match "Blue" for "Navy Blue" and "Red" for "Team Red" — close, but not it.
  const candidates = [
    ...tokens.map((t) => t.toLowerCase().replace(/\s+/g, "")),
    ...tokens.flatMap((t) => t.toLowerCase().split(/\s+/)),
  ];
  for (const candidate of candidates) {
    if (!candidate) continue;
    probe.style.color = "";
    probe.style.color = candidate;
    if (probe.style.color) return candidate;
  }
  return null;
}

/**
 * The price to display once a variant is chosen.
 *
 * Variants on a discounted product routinely restate the *sale* price without restating what
 * it was reduced from — Nike's sizes each carry £76.99 with no `compare_at_price`, while the
 * product carries £76.99 reduced from £109.99. Taking the variant's price wholesale therefore
 * made the strikethrough and the "Save 30%" badge vanish the moment a shopper picked a size,
 * on a product that was still on sale at exactly the same number.
 *
 * So: a variant that genuinely costs something different wins outright, and a variant that
 * merely restates the product's price inherits the discount context it left out.
 */
export function effectivePrice(base: Price, variant: Price | null | undefined): Price {
  if (!variant) return base;
  if (variant.price !== base.price || variant.currency !== base.currency) return variant;
  return variant.compare_at_price ? variant : base;
}
