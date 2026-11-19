import { cn } from "@/lib/utils";

/** Compact HypoClips mark used anywhere the old raster logo appeared. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span aria-hidden="true" className={cn("inline-flex shrink-0 items-center justify-center rounded-md bg-gradient-to-br from-[#b4ff5a] to-[#7fff00] font-display font-extrabold text-[#061000] shadow-[0_0_18px_rgb(127_255_0/0.22)]", className)}>
      H
    </span>
  );
}
