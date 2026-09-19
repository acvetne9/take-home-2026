import { useMemo } from "react";
import type { Variant, VariantOption } from "@/lib/types";
import { cn, swatchColor } from "@/lib/utils";

/**
 * The variant picker, driven entirely by `options` + `variants` from the extractor.
 *
 * This is where the backend's schema choice becomes visible. Because the axes and the concrete
 * SKUs are published separately, the picker can do the thing shoppers expect and grey out
 * combinations that do not exist — a Medium that is only made in Regular fit, say. A flat list
 * of variant strings could not express that, and a list of SKUs alone would make every
 * consumer re-derive the axes before it could render anything.
 *
 * Availability is computed against the *other* selected axes, which is the rule real storefronts
 * use: choosing a colour should show you which sizes that colour comes in, not grey out the
 * colour you just chose.
 */
export function VariantPicker({
  options,
  variants,
  selection,
  onChange,
}: {
  options: VariantOption[];
  variants: Variant[];
  selection: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}) {
  const reachable = useMemo(() => {
    const map: Record<string, Set<string>> = {};
    for (const axis of options) {
      const others = Object.entries(selection).filter(([name]) => name !== axis.name);
      const usable = variants.filter(
        (v) =>
          v.availability !== "out_of_stock" &&
          v.availability !== "discontinued" &&
          others.every(([name, value]) => !v.options[name] || v.options[name] === value),
      );
      map[axis.name] = new Set(usable.map((v) => v.options[axis.name]).filter(Boolean));
    }
    return map;
  }, [options, variants, selection]);

  if (!options.length) return null;

  return (
    <div className="space-y-5">
      {options.map((axis) => (
        <fieldset key={axis.name}>
          <legend className="mb-2 flex w-full items-baseline justify-between text-sm">
            <span className="font-medium">{axis.name}</span>
            <span className="text-muted-fg">{selection[axis.name] ?? "Select"}</span>
          </legend>
          <div className="flex flex-wrap gap-2">
            {axis.values.map((value) => {
              const selected = selection[axis.name] === value;
              const available = reachable[axis.name]?.has(value) ?? true;
              const swatch = axis.name.toLowerCase() === "color" ? swatchColor(value) : null;
              return (
                <button
                  key={value}
                  type="button"
                  aria-pressed={selected}
                  aria-label={`${axis.name} ${value}${available ? "" : " (unavailable)"}`}
                  title={available ? value : `${value} — unavailable`}
                  onClick={() =>
                    onChange(
                      selected
                        ? Object.fromEntries(
                            Object.entries(selection).filter(([n]) => n !== axis.name),
                          )
                        : { ...selection, [axis.name]: value },
                    )
                  }
                  className={cn(
                    "relative rounded-md border px-3 py-1.5 text-sm transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    selected
                      ? "border-fg bg-fg text-bg"
                      : "border-line hover:border-fg/40 hover:bg-muted",
                    // Unavailable combinations stay selectable, so a shopper can still see the
                    // variant's details; they are struck through rather than removed.
                    !available && "text-muted-fg line-through decoration-muted-fg/60",
                  )}
                >
                  {swatch && (
                    <span
                      aria-hidden="true"
                      className="mr-1.5 inline-block size-3 translate-y-[1px] rounded-full border border-line"
                      style={{ background: swatch }}
                    />
                  )}
                  {value}
                </button>
              );
            })}
          </div>
        </fieldset>
      ))}
    </div>
  );
}

export { matchVariant } from "@/lib/variants";
