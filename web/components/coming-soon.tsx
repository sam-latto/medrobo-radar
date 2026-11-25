"use client";

import { useSession } from "@/components/app-shell";

/** Stand-in for screens not built yet in Phase E. */
export function ComingSoon({ title, description }: { title: string; description: string }) {
  const { me, industryId } = useSession();
  const industry = me.subscriptions.find((s) => s.industry_id === industryId)?.name;
  return (
    <div className="flex max-w-[680px] flex-col gap-3">
      {industry && <span className="text-overline uppercase text-muted">{industry}</span>}
      <h1 className="text-h1">{title}</h1>
      <p className="text-body-lg text-muted">{description}</p>
    </div>
  );
}
