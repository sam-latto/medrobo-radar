import { ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";

/** A source shown by name, opening in a new tab (docs/DESIGN.md "Source link"). */
export function SourceLink({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn("inline-flex items-center gap-1 font-medium text-teal no-underline hover:underline", className)}
    >
      {children}
      <ExternalLink size={13} strokeWidth={1.75} aria-hidden />
      <span className="sr-only">(opens in new tab)</span>
    </a>
  );
}
