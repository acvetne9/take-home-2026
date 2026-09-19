/**
 * The handful of primitives this app needs, in the shadcn idiom: unstyled semantics, variants
 * via `cva`, composition via `cn`, and all colour expressed through CSS custom properties so
 * light and dark come from one token set rather than two branches.
 *
 * Written here rather than pulled in wholesale because the app needs four components and the
 * point of shadcn is that the code is yours to keep.
 */
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const focus =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background";

const buttonVariants = cva(
  `inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm
   font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 ${focus}`,
  {
    variants: {
      variant: {
        default: "bg-fg text-bg hover:bg-fg/90",
        outline: "border border-line bg-transparent hover:bg-muted",
        ghost: "hover:bg-muted",
      },
      size: { default: "h-10 px-4 py-2", sm: "h-8 px-3 text-xs", icon: "h-9 w-9" },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />
  ),
);
Button.displayName = "Button";

export function Badge({
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border border-line bg-muted/60 px-2.5 py-0.5",
        "text-[11px] font-medium tracking-wide text-muted-fg",
        className,
      )}
      {...props}
    />
  );
}

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-xl border border-line bg-surface overflow-hidden", className)}
      {...props}
    />
  );
}

/** Pulses only when the viewer has not asked for reduced motion. */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-md bg-muted motion-safe:animate-pulse", className)}
      aria-hidden="true"
      {...props}
    />
  );
}
