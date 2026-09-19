import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Play, ZoomIn, ZoomOut } from "lucide-react";
import { ProductImage } from "./ProductImage";
import { Button } from "./ui/primitives";
import { cn } from "@/lib/utils";

/**
 * Full-resolution gallery with thumbnails, arrow-key navigation and an optional video.
 *
 * Keyboard navigation is scoped to the gallery's own focus rather than bound to the window:
 * a global arrow-key handler on a product page fights with the page scroll and with every
 * other control on it.
 */
export function Gallery({
  images,
  videoUrl,
  alt,
  transitionName,
}: {
  images: string[];
  videoUrl: string | null;
  alt: string;
  transitionName?: string;
}) {
  const [index, setIndex] = useState(0);
  const [showVideo, setShowVideo] = useState(false);
  // Zoom is the one thing a shopper reaches for that a static gallery cannot answer: is that
  // a knit or a weave, is that seam stitched or glued. The extractor already resolves the
  // full-resolution asset, so magnifying costs nothing but the transform.
  const [zoomed, setZoomed] = useState(false);
  const [origin, setOrigin] = useState({ x: 50, y: 50 });
  const stripRef = useRef<HTMLDivElement>(null);

  useEffect(() => setIndex(0), [images]);

  const go = (delta: number) => {
    setShowVideo(false);
    setZoomed(false);
    setIndex((i) => (i + delta + images.length) % images.length);
  };

  // Pan by pointer position rather than by drag: the pointer is already where the shopper is
  // looking, so the magnified region follows attention without a second gesture to learn.
  const track = (e: React.MouseEvent<HTMLElement>) => {
    if (!zoomed) return;
    const box = e.currentTarget.getBoundingClientRect();
    setOrigin({
      x: ((e.clientX - box.left) / box.width) * 100,
      y: ((e.clientY - box.top) / box.height) * 100,
    });
  };

  useEffect(() => {
    stripRef.current
      ?.querySelector<HTMLElement>(`[data-thumb="${index}"]`)
      ?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
  }, [index]);

  if (!images.length) {
    return <ProductImage alt={alt} ratio="square" className="rounded-xl border border-line" />;
  }

  return (
    <div className="flex flex-col-reverse gap-3 md:flex-row md:items-start">
      <div
        ref={stripRef}
        className={cn(
          "flex gap-2 overflow-x-auto pb-1 md:flex-col md:overflow-y-auto md:pb-0",
          // Matched to the hero's height so the strip scrolls rather than clipping a thumbnail
          // halfway, which reads as a layout bug rather than as "there are more images".
          "md:h-[min(70vh,640px)] md:pr-1",
        )}
        role="tablist"
        aria-label="Product images"
      >
        {videoUrl && (
          <button
            type="button"
            role="tab"
            aria-selected={showVideo}
            onClick={() => setShowVideo(true)}
            className={cn(
              "relative grid size-16 shrink-0 place-items-center rounded-md border bg-muted",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              showVideo ? "border-fg" : "border-line hover:border-fg/40",
            )}
          >
            <Play className="size-5" aria-hidden="true" />
            <span className="sr-only">Play product video</span>
          </button>
        )}
        {images.map((url, i) => (
          <button
            key={url}
            type="button"
            role="tab"
            data-thumb={i}
            aria-selected={!showVideo && i === index}
            aria-label={`${alt} — image ${i + 1} of ${images.length}`}
            onClick={() => {
              setShowVideo(false);
              setIndex(i);
            }}
            className={cn(
              "size-16 shrink-0 overflow-hidden rounded-md border transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              !showVideo && i === index ? "border-fg" : "border-line hover:border-fg/40",
            )}
          >
            <ProductImage src={url} alt="" ratio="square" />
          </button>
        ))}
      </div>

      <div
        className="group relative w-full flex-1 self-start focus-visible:outline-none"
        tabIndex={0}
        role="group"
        aria-label="Product image viewer — use arrow keys, Z to zoom"
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
          if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
          if (e.key.toLowerCase() === "z") { e.preventDefault(); setZoomed((v) => !v); }
          if (e.key === "Escape" && zoomed) { e.preventDefault(); setZoomed(false); }
        }}
      >
        {showVideo && videoUrl ? (
          <video
            src={videoUrl}
            controls
            autoPlay
            playsInline
            className="aspect-square w-full rounded-xl border border-line bg-black object-contain"
          />
        ) : (
          <div
            onMouseMove={track}
            onMouseLeave={() => setOrigin({ x: 50, y: 50 })}
            onClick={() => setZoomed((v) => !v)}
            className={cn(
              "overflow-hidden rounded-xl border border-line",
              zoomed ? "cursor-zoom-out" : "cursor-zoom-in",
              // The transform lands on the <img> inside ProductImage, so the frame stays put
              // and only its contents magnify — the border and aspect ratio never move.
              "[&_img]:transition-transform [&_img]:duration-200 motion-reduce:[&_img]:transition-none",
              zoomed && "[&_img]:scale-250",
            )}
            style={{ ["--ox" as string]: `${origin.x}%`, ["--oy" as string]: `${origin.y}%` }}
          >
            <ProductImage
              src={images[index]}
              alt={`${alt} — image ${index + 1} of ${images.length}`}
              ratio="square"
              priority
              transitionName={index === 0 ? transitionName : undefined}
              className="[&_img]:[transform-origin:var(--ox)_var(--oy)]"
            />
          </div>
        )}

        {!showVideo && images.length > 0 && (
          <Button
            variant="outline"
            size="icon"
            aria-pressed={zoomed}
            aria-label={zoomed ? "Zoom out" : "Zoom in"}
            onClick={() => setZoomed((v) => !v)}
            className="absolute right-3 top-3 rounded-full bg-surface/90 opacity-0 backdrop-blur transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
          >
            {zoomed ? <ZoomOut className="size-4" aria-hidden="true" /> : <ZoomIn className="size-4" aria-hidden="true" />}
          </Button>
        )}

        {images.length > 1 && !showVideo && (
          <>
            <Button
              variant="outline" size="icon" aria-label="Previous image"
              onClick={() => go(-1)}
              className="absolute left-3 top-1/2 -translate-y-1/2 rounded-full bg-surface/90 opacity-0 backdrop-blur transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
            >
              <ChevronLeft className="size-4" aria-hidden="true" />
            </Button>
            <Button
              variant="outline" size="icon" aria-label="Next image"
              onClick={() => go(1)}
              className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full bg-surface/90 opacity-0 backdrop-blur transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
            >
              <ChevronRight className="size-4" aria-hidden="true" />
            </Button>
            <div className="pointer-events-none absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full bg-surface/90 px-2.5 py-1 text-[11px] tabular-nums text-muted-fg backdrop-blur">
              {index + 1} / {images.length}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
