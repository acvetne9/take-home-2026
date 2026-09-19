import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Check, Info, X } from "lucide-react";
import { fetchProduct } from "@/lib/api";
import type { CatalogEntry } from "@/lib/types";
import { Badge, Button, Card, Skeleton } from "@/components/ui/primitives";
import { Gallery } from "@/components/Gallery";
import { OfferList } from "@/components/OfferList";
import { Specs } from "@/components/Specs";
import { VariantPicker, matchVariant } from "@/components/VariantPicker";
import { offersFor } from "@/lib/offers";
import { categorySegments, discountPercent, effectivePrice, formatPrice } from "@/lib/utils";

const AVAILABILITY: Record<string, { label: string; tone: string }> = {
  in_stock: { label: "In stock", tone: "text-emerald-600 dark:text-emerald-400" },
  out_of_stock: { label: "Out of stock", tone: "text-muted-fg" },
  preorder: { label: "Pre-order", tone: "text-amber-600 dark:text-amber-400" },
  discontinued: { label: "Discontinued", tone: "text-muted-fg" },
  unknown: { label: "", tone: "" },
};

export default function ProductDetail() {
  const { id = "" } = useParams();
  const [entry, setEntry] = useState<CatalogEntry | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selection, setSelection] = useState<Record<string, string>>({});

  useEffect(() => {
    let live = true;
    setEntry(null);
    setError(null);
    setSelection({});
    fetchProduct(id)
      .then((d) => live && setEntry(d))
      .catch((e) => live && setError(e.message));
    window.scrollTo({ top: 0 });
    return () => {
      live = false;
    };
  }, [id]);

  // The tab is how someone tells two open products apart, so it has to carry the product's
  // name rather than the app's. Reset on unmount so a back-navigation does not leave it stale.
  useEffect(() => {
    if (!entry?.product) return;
    document.title = `${entry.product.name} — ${entry.product.brand}`;
    return () => {
      document.title = "Catalog";
    };
  }, [entry]);

  const product = entry?.product;
  const selected = useMemo(
    () => (product ? matchVariant(product.variants, product.options, selection) : null),
    [product, selection],
  );

  if (error === "not-found") {
    return (
      <Centered
        title="Product not found"
        body="That product is not in this catalog. It may have been removed or never extracted."
      />
    );
  }
  if (error) return <Centered title="Could not load this product" body={error} />;
  if (!product || !entry) return <DetailSkeleton />;

  // A variant that genuinely costs something different wins; one that merely restates the
  // product's price keeps the discount context it omitted. See `effectivePrice`.
  const price = effectivePrice(product.price, selected?.price);
  const discount = discountPercent(price);
  const segments = categorySegments(product.category.name);
  const availability = AVAILABILITY[selected?.availability ?? "unknown"];
  // A variant's own photographs lead the gallery; the product's full set follows, deduplicated.
  // Replacing the set outright was wrong: Nike states one image per size, so choosing a size
  // collapsed the gallery from eight photographs to one — a shopper loses the product by
  // answering a question about it. Leading rather than replacing gets the intent (show me this
  // colour) without the loss.
  const images = selected?.image_urls.length
    ? [...selected.image_urls, ...product.image_urls.filter((u) => !selected.image_urls.includes(u))]
    : product.image_urls;

  return (
    <div className="mx-auto max-w-[1400px] px-5 py-8 sm:px-8">
      <Link
        to="/"
        viewTransition
        className="mb-6 inline-flex items-center gap-1.5 rounded-md text-sm text-muted-fg transition-colors hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        Back to catalog
      </Link>

      <div className="grid gap-10 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)] lg:gap-14">
        <Gallery
          images={images}
          videoUrl={product.video_url}
          alt={product.name}
          transitionName={`product-image-${entry.id}`}
        />

        <div className="lg:sticky lg:top-8 lg:self-start">
          <nav aria-label="Category" className="mb-3 flex flex-wrap items-center gap-1 text-[11px] text-muted-fg">
            {segments.map((segment, i) => (
              <span key={segment} className="flex items-center gap-1">
                {i > 0 && <span aria-hidden="true">/</span>}
                <span className={i === segments.length - 1 ? "text-fg" : undefined}>{segment}</span>
              </span>
            ))}
          </nav>

          <p className="text-xs font-medium uppercase tracking-widest text-muted-fg">
            {product.brand}
          </p>
          <h1 className="mt-1.5 text-2xl font-semibold leading-tight tracking-tight sm:text-3xl">
            {product.name}
          </h1>

          <div className="mt-4 flex flex-wrap items-baseline gap-3">
            <span className="text-2xl font-semibold tabular-nums">
              {formatPrice(price.price, price.currency)}
            </span>
            {price.compare_at_price && (
              <>
                <span className="text-base tabular-nums text-muted-fg line-through">
                  {formatPrice(price.compare_at_price, price.currency)}
                </span>
                <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-semibold text-white">
                  Save {discount}%
                </span>
              </>
            )}
          </div>

          {availability.label && (
            <p className={`mt-2 flex items-center gap-1.5 text-sm ${availability.tone}`}>
              {selected?.availability === "in_stock" ? (
                <Check className="size-4" aria-hidden="true" />
              ) : (
                <X className="size-4" aria-hidden="true" />
              )}
              {availability.label}
            </p>
          )}

          {product.options.length > 0 && (
            <div className="mt-7 border-t border-line pt-7">
              <VariantPicker
                options={product.options}
                variants={product.variants}
                selection={selection}
                onChange={setSelection}
              />
              <p className="mt-4 min-h-5 text-xs text-muted-fg">
                {selected
                  ? `1 of ${product.variants.length} configurations`
                  : `Select ${product.options
                      .filter((o) => !selection[o.name])
                      .map((o) => o.name.toLowerCase())
                      .join(" and ")} — ${product.variants.length} configurations available`}
              </p>
            </div>
          )}

          {/*
            The primary action. It is deliberately below the picker: the offer's price and
            availability are the selected variant's, so asking the shopper to choose first is
            what makes the number underneath it true.
          */}
          <div className="mt-7 border-t border-line pt-7">
            <OfferList offers={offersFor(entry, selected)} />
          </div>

          {product.key_features.length > 0 && (
            <section className="mt-7 border-t border-line pt-7">
              <h2 className="mb-3 text-sm font-medium">Key features</h2>
              <ul className="space-y-2">
                {product.key_features.map((feature) => (
                  <li key={feature} className="flex gap-2.5 text-sm text-muted-fg">
                    <Check className="mt-0.5 size-4 shrink-0 text-fg/40" aria-hidden="true" />
                    <span>{feature}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="mt-7 border-t border-line pt-7">
            <h2 className="mb-3 text-sm font-medium">Description</h2>
            <p className="whitespace-pre-line text-sm leading-relaxed text-muted-fg">
              {product.description}
            </p>
          </section>

          <Specs product={product} selected={selected} />

          <Provenance entry={entry} />
        </div>
      </div>
    </div>
  );
}

/**
 * Where each field came from, and how much the extractor trusts the row.
 *
 * This is unusual on a storefront and deliberate here: the product of this exercise is
 * extracted data, and the most useful thing a reviewer can see is that the price came from a
 * schema.org annotation rather than from a regex over rendered text.
 */
function Provenance({ entry }: { entry: CatalogEntry }) {
  const [open, setOpen] = useState(false);
  const sources = Object.entries(entry.field_sources);

  return (
    <section className="mt-7 border-t border-line pt-7">
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="-ml-3 text-muted-fg"
      >
        <Info className="size-4" aria-hidden="true" />
        Extraction details
        <Badge className="ml-1">{Math.round(entry.confidence * 100)}% confidence</Badge>
      </Button>

      {open && (
        <Card className="mt-3 space-y-3 p-4 text-xs">
          {entry.url && (
            <div>
              <p className="mb-1 font-medium">Source page</p>
              <a
                href={entry.url}
                target="_blank"
                rel="noreferrer"
                className="break-all text-muted-fg underline decoration-line underline-offset-2 hover:text-fg"
              >
                {entry.url}
              </a>
            </div>
          )}
          {sources.length > 0 && (
            <div>
              <p className="mb-1 font-medium">Field provenance</p>
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-muted-fg">
                {sources.map(([field, source]) => (
                  <div key={field} className="contents">
                    <dt>{field}</dt>
                    <dd className="truncate font-mono text-[11px]" title={source}>
                      {source}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
          {entry.warnings.length > 0 && (
            <div>
              <p className="mb-1 font-medium">Warnings</p>
              <ul className="list-disc space-y-0.5 pl-4 text-muted-fg">
                {entry.warnings.map((w) => <li key={w}>{w}</li>)}
              </ul>
            </div>
          )}
        </Card>
      )}
    </section>
  );
}

function DetailSkeleton() {
  return (
    <div className="mx-auto max-w-[1400px] px-5 py-8 sm:px-8">
      <Skeleton className="mb-6 h-4 w-28" />
      <div className="grid gap-10 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)] lg:gap-14">
        <Skeleton className="aspect-square rounded-xl" />
        <div className="space-y-4">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-8 w-3/4" />
          <Skeleton className="h-7 w-28" />
          <Skeleton className="h-24 w-full" />
        </div>
      </div>
    </div>
  );
}

function Centered({ title, body }: { title: string; body: string }) {
  return (
    <div className="mx-auto grid min-h-[70vh] max-w-md place-items-center px-6 text-center">
      <div>
        <h1 className="text-lg font-semibold">{title}</h1>
        <p className="mt-2 text-sm text-muted-fg">{body}</p>
        <Link
          to="/"
          className="mt-6 inline-flex h-10 items-center rounded-md bg-fg px-4 text-sm font-medium text-bg transition-colors hover:bg-fg/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Back to catalog
        </Link>
      </div>
    </div>
  );
}
