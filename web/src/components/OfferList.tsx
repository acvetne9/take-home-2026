import { ExternalLink } from "lucide-react";
import type { Offer } from "@/lib/types";
import { Badge } from "./ui/primitives";
import { cn, discountPercent, formatPrice } from "@/lib/utils";

/**
 * The merchants selling this product, cheapest first.
 *
 * This is the PDP's primary action, and it is a *list* rather than a button on purpose. The
 * extractor reads one page and so produces one offer today; a catalog that has resolved the
 * same physical product across retailers produces several, and the only difference here is the
 * number of rows. Sorting by price and labelling the leader is what makes the list worth being
 * a list at all.
 *
 * `rel="sponsored"` because an outbound merchant link from a catalog like this is an affiliate
 * link in every real deployment, and mislabelling it is a search-policy problem rather than a
 * styling one.
 */
export function OfferList({ offers }: { offers: Offer[] }) {
  if (!offers.length) {
    return (
      <p className="rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm text-muted-fg">
        No merchant is currently listing this product.
      </p>
    );
  }

  const sorted = [...offers].sort((a, b) => a.price.price - b.price.price);
  const inStock = sorted.filter((o) => o.availability !== "out_of_stock" && o.availability !== "discontinued");
  const unavailable = sorted.filter((o) => !inStock.includes(o));
  // The leader is the cheapest one you can actually buy, falling back to the cheapest listing
  // when nothing is in stock — a "best price" badge on an unbuyable row would be a lie.
  const lead = inStock[0] ?? sorted[0];

  return (
    <div data-slot="offer-list" className="flex flex-col gap-2">
      {inStock.map((offer) => (
        <OfferRow key={offer.url} offer={offer} isLead={offer === lead && sorted.length > 1} />
      ))}
      {unavailable.length > 0 && (
        <>
          <p className="pt-2 text-xs font-medium text-muted-fg">Out of stock</p>
          {unavailable.map((offer) => (
            <OfferRow key={offer.url} offer={offer} isLead={false} dimmed />
          ))}
        </>
      )}
    </div>
  );
}

function OfferRow({
  offer,
  isLead,
  dimmed = false,
}: {
  offer: Offer;
  isLead: boolean;
  dimmed?: boolean;
}) {
  const discount = discountPercent(offer.price);

  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-between gap-x-4 gap-y-3 rounded-lg border border-line p-3",
        dimmed && "opacity-60",
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-sm font-medium">{offer.domain}</span>
        {isLead && <Badge>Best price</Badge>}
      </div>

      <div className="flex items-center gap-3">
        <div className="flex items-baseline gap-2">
          {offer.price.compare_at_price && (
            <span className="text-xs tabular-nums text-muted-fg line-through">
              {formatPrice(offer.price.compare_at_price, offer.price.currency)}
            </span>
          )}
          <span className="text-sm font-semibold tabular-nums">
            {formatPrice(offer.price.price, offer.price.currency)}
          </span>
          {discount !== null && (
            <span className="text-xs font-medium text-accent">−{discount}%</span>
          )}
        </div>

        <a
          href={offer.url}
          target="_blank"
          rel="sponsored noopener noreferrer"
          className={cn(
            "inline-flex h-9 items-center gap-1.5 rounded-md bg-fg px-4 text-sm font-medium text-bg",
            "transition-colors hover:bg-fg/90",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
          )}
        >
          View at {offer.domain}
          <ExternalLink className="size-3.5" aria-hidden="true" />
        </a>
      </div>
    </div>
  );
}
