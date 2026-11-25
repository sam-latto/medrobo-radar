"use client";

import { useState } from "react";
import { Check, Plus } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Watch / Watching pill toggle (docs/DESIGN.md "Pill toggle"). Updates right away and rolls back
 * if the API refuses (e.g. the 500-company limit), reporting the message through `onError`.
 */
export function WatchButton({
  companyId,
  companyName,
  watching,
  onChange,
  onError,
}: {
  companyId: number;
  companyName: string;
  watching: boolean;
  onChange: (companyId: number, watching: boolean) => void;
  onError?: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);

  async function toggle() {
    const next = !watching;
    setBusy(true);
    onChange(companyId, next);
    try {
      await api(`/watchlist/${companyId}`, { method: next ? "PUT" : "DELETE" });
    } catch (e) {
      onChange(companyId, !next);
      onError?.(e instanceof ApiError ? e.message : "We couldn't update your watchlist. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={busy}
      aria-pressed={watching}
      aria-label={watching ? `Stop watching ${companyName}` : `Watch ${companyName}`}
      className={cn(
        "inline-flex h-[30px] cursor-pointer items-center gap-1.5 rounded-full border px-2.5 text-label font-semibold whitespace-nowrap",
        watching
          ? "border-teal-tint bg-teal-tint text-teal-dark"
          : "border-border-strong bg-white text-teal hover:border-teal",
        "disabled:cursor-default",
      )}
    >
      {watching ? <Check size={12} strokeWidth={2.5} aria-hidden /> : <Plus size={12} strokeWidth={2.5} aria-hidden />}
      {watching ? "Watching" : "Watch"}
    </button>
  );
}
