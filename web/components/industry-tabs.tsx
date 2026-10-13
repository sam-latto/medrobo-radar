"use client";

import Link from "next/link";
import { Plus } from "lucide-react";
import { useSession } from "@/components/app-shell";
import { cn } from "@/lib/utils";

/**
 * The followed industries as tabs at the top of a page (Briefing, Events, Companies). The choice
 * is shared across pages. On phones the row scrolls sideways inside its own box.
 */
export function IndustryTabs() {
  const { me, industryId, setIndustryId } = useSession();
  return (
    <div className="-mx-4 flex items-end gap-1 overflow-x-auto overflow-y-hidden px-4 shadow-[inset_0_-1px_0_var(--color-border)] desk:mx-0 desk:px-0 print:hidden">
      <div role="group" aria-label="Industry" className="flex items-end gap-1">
        {me.subscriptions.map((s) => {
          const active = s.industry_id === industryId;
          return (
            <button
              key={s.industry_id}
              type="button"
              aria-pressed={active}
              onClick={() => setIndustryId(s.industry_id)}
              className={cn(
                "h-11 shrink-0 cursor-pointer border-b-2 px-3 text-body whitespace-nowrap",
                active
                  ? "border-teal font-semibold text-teal-dark"
                  : "border-transparent font-medium text-ink-3 hover:text-ink",
              )}
            >
              {s.name}
            </button>
          );
        })}
      </div>
      <Link
        href="/industries"
        className="ml-1 inline-flex h-11 shrink-0 items-center gap-1 px-2 text-small font-medium whitespace-nowrap no-underline"
      >
        <Plus size={14} strokeWidth={2} aria-hidden />
        Edit
      </Link>
    </div>
  );
}
