import { useState } from "react";
import { cn } from "@/lib/utils";

/**
 * An image with a locked aspect ratio, a skeleton until it decodes, and a real fallback.
 *
 * The ratio is locked because these URLs point at other companies' CDNs: we do not know the
 * intrinsic dimensions ahead of time, and a grid that reflows as each image lands is the most
 * visible quality problem a catalog can have. The fallback matters for the same reason — an
 * extracted URL can 404 or be hotlink-blocked, and a broken-image glyph in a product grid
 * reads as a broken product.
 *
 * `fit` defaults to `contain`, which is the one that survives a mixed catalog. These images
 * come from five different photographers' conventions — a square white-background tool shot
 * next to a tall on-model shot next to a styled interior — and cropping them to a common tile
 * cuts the lamp's shade off. Contained on the card's own surface, every product is whole and
 * the same size, which is what a normalised catalog is supposed to look like. Use `cover` only
 * where the frame is the subject.
 */
export function ProductImage({
  src,
  alt,
  className,
  ratio = "square",
  fit = "contain",
  priority = false,
  transitionName,
  onError,
  decorative = false,
}: {
  src?: string;
  alt: string;
  className?: string;
  ratio?: "square" | "portrait" | "auto";
  fit?: "contain" | "cover";
  priority?: boolean;
  transitionName?: string;
  /** Fires when the URL will not load, so a decorative layer can remove itself entirely. */
  onError?: () => void;
  /**
   * A decorative image says nothing when it fails. A missing *product* photo is information
   * and gets a message; a hover crossfade or a background flourish is not, and announcing its
   * absence is worse than its absence. This also closes the race that `onError` alone leaves
   * open — the message can never paint, rather than painting and then being removed.
   */
  decorative?: boolean;
}) {
  const [state, setState] = useState<"loading" | "ready" | "error">(src ? "loading" : "error");

  return (
    <div
      className={cn(
        "relative overflow-hidden bg-image",
        ratio === "square" && "aspect-square",
        ratio === "portrait" && "aspect-4/5",
        className,
      )}
    >
      {state === "loading" && <div className="absolute inset-0 motion-safe:animate-pulse bg-muted" />}
      {state === "error" ? (
        decorative ? null : (
          <div className="absolute inset-0 grid place-items-center bg-muted text-[11px] text-muted-fg">
            image unavailable
          </div>
        )
      ) : (
        <img
          src={src}
          alt={alt}
          loading={priority ? "eager" : "lazy"}
          decoding="async"
          fetchPriority={priority ? "high" : "auto"}
          onLoad={() => setState("ready")}
          onError={() => { setState("error"); onError?.(); }}
          style={transitionName ? { viewTransitionName: transitionName } : undefined}
          className={cn(
            "h-full w-full transition-opacity duration-300",
            fit === "contain" ? "object-contain" : "object-cover",
            state === "ready" ? "opacity-100" : "opacity-0",
          )}
        />
      )}
    </div>
  );
}
