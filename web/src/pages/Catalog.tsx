import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { fetchCatalog } from "@/lib/api";
import type { CatalogEntry, CatalogResponse } from "@/lib/types";
import { Badge, Card, Skeleton } from "@/components/ui/primitives";
import { ProductImage } from "@/components/ProductImage";
import { cn, categorySegments, discountPercent, formatPrice, swatchColor } from "@/lib/utils";

function ProductCard({ entry }: { entry: CatalogEntry }) {
  const { product } = entry;
  // Extracted URLs are other companies' CDNs and some of them are already dead — one of the
  // twelve on the lamp 404s. A hero that fails still says so, because a missing product photo
  // is information; a *decorative* hover layer that fails must disappear instead, or hovering
  // the card replaces a good photograph with the words "image unavailable".
  const [hoverBroken, setHoverBroken] = useState(false);
  const discount = discountPercent(product.price);
  const segments = categorySegments(product.category.name);
  // Resolve up front and only render the row when at least one name is a real colour: a row
  // of identical grey fallback dots reads as a rendering bug, not as colour information.
  const swatches = product.colors.slice(0, 5).map((c) => [c, swatchColor(c)] as const);
  const hasSwatches = swatches.some(([, css]) => css);

  return (
    <Link
      to={`/product/${entry.id}`}
      // startViewTransition is opt-in per navigation so only this one morphs.
      viewTransition
      className="group rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
    >
      <Card className="h-full transition-[border-color,box-shadow] duration-200 group-hover:border-fg/25 group-hover:shadow-sm">
        <div className="relative">
          <ProductImage
            src={product.image_urls[0]}
            alt={product.name}
            ratio="square"
            transitionName={`product-image-${entry.id}`}
          />
          {/*
            Crossfade to the second shot on hover. A catalog's second image is almost always
            the contextual one — the garment on a body, the lamp in a room — which answers
            "what is this actually like" without costing a navigation. Hidden from assistive
            tech and skipped when there is only one image, so it never becomes a duplicate
            announcement or an empty fade.
          */}
          {product.image_urls[1] && !hoverBroken && (
            <div
              aria-hidden="true"
              className="absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100 motion-reduce:transition-none"
            >
              <ProductImage
                src={product.image_urls[1]}
                alt=""
                ratio="square"
                decorative
                onError={() => setHoverBroken(true)}
              />
            </div>
          )}
          {discount !== null && (
            <span className="absolute left-3 top-3 rounded-full bg-accent px-2 py-0.5 text-[11px] font-semibold text-white">
              −{discount}%
            </span>
          )}
          {product.video_url && (
            <span className="absolute right-3 top-3 rounded-full bg-surface/90 px-2 py-0.5 text-[11px] text-muted-fg backdrop-blur">
              video
            </span>
          )}
        </div>

        <div className="space-y-2 p-4">
          <p className="text-[11px] font-medium uppercase tracking-widest text-muted-fg">
            {product.brand}
          </p>
          <h2 className="line-clamp-2 text-sm font-medium leading-snug">{product.name}</h2>

          <div className="flex items-baseline gap-2">
            <span className="text-sm font-semibold tabular-nums">
              {formatPrice(product.price.price, product.price.currency)}
            </span>
            {product.price.compare_at_price && (
              <span className="text-xs tabular-nums text-muted-fg line-through">
                {formatPrice(product.price.compare_at_price, product.price.currency)}
              </span>
            )}
          </div>

          <div className="flex items-center justify-between gap-2 pt-1">
            <Badge className="max-w-[70%] truncate" title={product.category.name}>
              {segments[segments.length - 1]}
            </Badge>
            {hasSwatches && (
              <div className="flex items-center gap-1" aria-label={`${product.colors.length} colours`}>
                {swatches.map(([color, css]) => (
                  <span
                    key={color}
                    title={color}
                    className={cn("size-3 rounded-full border border-line", !css && "bg-muted")}
                    style={css ? { background: css } : undefined}
                  />
                ))}
                {product.colors.length > swatches.length && (
                  <span className="text-[10px] text-muted-fg">
                    +{product.colors.length - swatches.length}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
      </Card>
    </Link>
  );
}

function CardSkeleton() {
  return (
    <Card>
      <Skeleton className="aspect-square rounded-none" />
      <div className="space-y-2 p-4">
        <Skeleton className="h-2.5 w-16" />
        <Skeleton className="h-3.5 w-full" />
        <Skeleton className="h-3.5 w-2/3" />
        <Skeleton className="h-4 w-20" />
      </div>
    </Card>
  );
}

type Filter = { kind: "brand" | "category"; value: string } | null;

// Past these counts the chip row stops being scannable and becomes the page. Chosen from what
// fits one line at a typical width, not from this catalog — five brands is not the interesting
// case, fifty is.
const BRAND_CHIP_LIMIT = 8;
const ROOT_CHIP_LIMIT = 8;

export default function Catalog() {
  useEffect(() => {
    document.title = "Catalog";
  }, []);

  const [data, setData] = useState<CatalogResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>(null);

  useEffect(() => {
    let live = true;
    fetchCatalog()
      .then((d) => live && setData(d))
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, []);

  // Filtering client-side: the catalog is one request, so a round trip per click would add
  // latency without adding anything. The API supports server-side filtering for when it is not.
  const shown = useMemo(() => {
    if (!data) return [];
    if (!filter) return data.products;
    return data.products.filter((e) =>
      filter.kind === "brand"
        ? e.product.brand === filter.value
        : e.product.category.name.startsWith(filter.value),
    );
  }, [data, filter]);

  const roots = useMemo(
    () => [...new Set((data?.categories ?? []).map((c) => categorySegments(c)[0]))].sort(),
    [data],
  );

  return (
    <div className="mx-auto max-w-[1400px] px-5 py-10 sm:px-8">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Catalog</h1>
        <p className="mt-1 text-sm text-muted-fg">
          {data ? `${data.total} products` : "Loading products"} extracted from raw product
          detail pages.
        </p>
      </header>

      {data && data.total > 0 && (
        <div className="mb-7 flex flex-wrap items-center gap-2">
          <FilterChip active={!filter} onClick={() => setFilter(null)}>
            All
          </FilterChip>

          {/*
            Chips enumerate the axis; a select names it. Five brands read best as chips you can
            see all of at once, and the same markup at fifty is a wall of them that pushes the
            products themselves below the fold. So the control changes shape with the data it
            describes rather than assuming the size of this catalog. The taxonomy has 21 roots,
            which is bounded and small, so those stay chips at any catalog size.
          */}
          {data.brands.length <= BRAND_CHIP_LIMIT ? (
            data.brands.map((brand) => (
              <FilterChip
                key={brand}
                active={filter?.kind === "brand" && filter.value === brand}
                onClick={() => setFilter({ kind: "brand", value: brand })}
              >
                {brand}
              </FilterChip>
            ))
          ) : (
            <FilterSelect
              label="Brand"
              value={filter?.kind === "brand" ? filter.value : ""}
              options={data.brands}
              onChange={(v) => setFilter(v ? { kind: "brand", value: v } : null)}
            />
          )}

          <span className="mx-1 h-4 w-px bg-line" aria-hidden="true" />

          {roots.length <= ROOT_CHIP_LIMIT ? (
            roots.map((root) => (
              <FilterChip
                key={root}
                active={filter?.kind === "category" && filter.value === root}
                onClick={() => setFilter({ kind: "category", value: root })}
              >
                {root}
              </FilterChip>
            ))
          ) : (
            <FilterSelect
              label="Category"
              value={filter?.kind === "category" ? filter.value : ""}
              options={roots}
              onChange={(v) => setFilter(v ? { kind: "category", value: v } : null)}
            />
          )}
        </div>
      )}

      {error && (
        <EmptyState
          title="Could not load the catalog"
          body={`${error}. Start the API with \`python server.py\`, then reload.`}
        />
      )}

      {!data && !error && (
        <div className="grid grid-cols-2 gap-5 md:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 8 }, (_, i) => <CardSkeleton key={i} />)}
        </div>
      )}

      {data && shown.length === 0 && !error && (
        <EmptyState
          title={data.total === 0 ? "No products yet" : "Nothing matches that filter"}
          body={
            data.total === 0
              ? "Run `python main.py` to extract products from ./data, then reload."
              : "Try a different brand or category."
          }
        />
      )}

      {shown.length > 0 && (
        <div className="grid grid-cols-2 gap-5 md:grid-cols-3 xl:grid-cols-4">
          {shown.map((entry) => (
            <ProductCard key={entry.id} entry={entry} />
          ))}
        </div>
      )}
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active ? "border-fg bg-fg text-bg" : "border-line text-muted-fg hover:border-fg/40 hover:text-fg",
      )}
    >
      {children}
    </button>
  );
}

/** The same filter, for when there are too many values to show at once. */
function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="inline-flex items-center gap-2">
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          "rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          value ? "border-fg bg-fg text-bg" : "border-line text-muted-fg hover:border-fg/40",
        )}
      >
        <option value="">{label}: any</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-xl border border-dashed border-line px-6 py-16 text-center">
      <p className="text-sm font-medium">{title}</p>
      <p className="mx-auto mt-1 max-w-md text-sm text-muted-fg">{body}</p>
    </div>
  );
}
