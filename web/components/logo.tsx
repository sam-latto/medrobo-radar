import { cn } from "@/lib/utils";

/** The radar mark beside "Industry Radar" (docs/DESIGN.md section 6). `onDark` draws white rings. */
export function Logo({ onDark = false, className }: { onDark?: boolean; className?: string }) {
  const ring = onDark ? "var(--color-white)" : "var(--color-teal)";
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <svg width="28" height="28" viewBox="0 0 28 28" fill="none" aria-hidden="true">
        <circle cx="14" cy="14" r="12" stroke={ring} strokeWidth="1.5" />
        <circle cx="14" cy="14" r="7" stroke={ring} strokeWidth="1.5" opacity="0.5" />
        <path d="M14 14 L22 6" stroke={ring} strokeWidth="1.5" strokeLinecap="round" />
        <circle cx="14" cy="14" r="2.5" fill={ring} />
        <circle cx="19.5" cy="8.5" r="2.25" fill="var(--color-amber)" />
      </svg>
      <span className="text-[16px] font-semibold tracking-[-0.01em]">Industry Radar</span>
    </div>
  );
}
