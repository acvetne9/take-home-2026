// Mirrors models.py. Kept hand-written rather than generated: the API surface is small, and a
// hand-written type is the place to document what the backend's fields actually mean.

export type Availability =
  | "in_stock" | "out_of_stock" | "preorder" | "discontinued" | "unknown";

export interface Price {
  price: number;
  currency: string;
  /** The pre-markdown price, present only when the page stated one. */
  compare_at_price: number | null;
}

/** An axis of choice — Size, Color, Fit. Values are in the order the page presented them. */
export interface VariantOption {
  name: string;
  values: string[];
}

/** One purchasable configuration. `options` maps each axis to this variant's value. */
export interface Variant {
  sku: string | null;
  options: Record<string, string>;
  price: Price | null;
  image_urls: string[];
  availability: Availability;
  gtin: string | null;
  mpn: string | null;
}

export interface Product {
  name: string;
  price: Price;
  description: string;
  key_features: string[];
  image_urls: string[];
  video_url: string | null;
  category: { name: string };
  brand: string;
  colors: string[];
  variants: Variant[];
  options: VariantOption[];
}

/**
 * One merchant's listing of a product. Derived client-side today (see `lib/offers.ts`) because
 * one PDP yields one listing; in a catalog that has resolved products across retailers this is
 * what the API returns a list of, and it is the row that carries the outbound link.
 */
export interface Offer {
  url: string;
  domain: string;
  price: Price;
  availability: Availability;
  sku: string | null;
}

export interface CatalogEntry {
  id: string;
  product: Product;
  url: string | null;
  source: string | null;
  /** How much the extractor trusts this row; surfaced rather than hidden. */
  confidence: number;
  warnings: string[];
  /** Which carrier each field came from, e.g. `price: "json:offers.price"`. */
  field_sources: Record<string, string>;
}

export interface CatalogResponse {
  total: number;
  brands: string[];
  categories: string[];
  products: CatalogEntry[];
}
