"use client";

import { useEffect, useMemo, useState } from "react";
import { Download, Search } from "lucide-react";
import { api, ApiError, download, type EventFilterOptions, type EventItem, type Paged } from "@/lib/api";
import { parseDate, shortDate } from "@/lib/format";
import { eventTypeLabel, segmentLabel } from "@/lib/labels";
import { useSession } from "@/components/app-shell";
import { IndustryTabs } from "@/components/industry-tabs";
import { SourceLink } from "@/components/source-link";
import { WatchButton } from "@/components/watch-button";
import { Button } from "@/components/ui/button";

const PAGE_SIZE = 25;
const DEFAULT_DAYS = 90;
const SEARCH_DELAY_MS = 300;

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

type Filters = { q: string; type: string; segment: string; from: string; to: string; focusOnly: boolean };
const defaultFilters = (): Filters => ({
  q: "",
  type: "",
  segment: "",
  from: isoDaysAgo(DEFAULT_DAYS),
  to: "",
  focusOnly: false,
});

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: Paged<EventItem> };

/** Remounted (via `key`) whenever the industry tab changes, so filters start fresh. */
export function EventsView() {
  const { me, industryId } = useSession();
  const subscription = me.subscriptions.find((s) => s.industry_id === industryId);
  const industryName = subscription?.name ?? "";
  const focus = subscription?.focus ?? null;

  const [filters, setFilters] = useState<Filters>(defaultFilters);
  const [search, setSearch] = useState(""); // what's typed; copied to filters.q after a pause
  const [page, setPage] = useState(1);
  const [options, setOptions] = useState<EventFilterOptions>({ types: [], segments: [] });
  const [state, setState] = useState<State>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const query = useMemo(() => {
    const p = new URLSearchParams({ industry_id: industryId });
    if (filters.q) p.set("q", filters.q);
    if (filters.type) p.set("type", filters.type);
    if (filters.segment) p.set("segment", filters.segment);
    if (filters.from) p.set("from", filters.from);
    if (filters.to) p.set("to", filters.to);
    if (filters.focusOnly) p.set("focus_only", "true");
    return p;
  }, [industryId, filters]);

  useEffect(() => {
    const ctrl = new AbortController();
    api<EventFilterOptions>(`/events/filters?industry_id=${encodeURIComponent(industryId)}`, { signal: ctrl.signal })
      .then(setOptions)
      .catch(() => {}); // the dropdowns just stay at "All"
    return () => ctrl.abort();
  }, [industryId]);

  useEffect(() => {
    const q = search.trim();
    if (q === filters.q) return;
    const timer = setTimeout(() => {
      setState({ status: "loading" });
      setFilters((f) => ({ ...f, q }));
      setPage(1);
    }, SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [search, filters.q]);

  useEffect(() => {
    const ctrl = new AbortController();
    const p = new URLSearchParams(query);
    p.set("page", String(page));
    p.set("page_size", String(PAGE_SIZE));
    api<Paged<EventItem>>(`/events?${p}`, { signal: ctrl.signal })
      .then((data) => setState({ status: "ready", data }))
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        setState({ status: "error", message: e instanceof ApiError ? e.message : "We couldn't load events." });
      });
    return () => ctrl.abort();
  }, [query, page, attempt]);

  function update(change: Partial<Filters>) {
    setState({ status: "loading" });
    setFilters((f) => ({ ...f, ...change }));
    setPage(1);
  }

  function clearFilters() {
    setSearch("");
    setState({ status: "loading" });
    setFilters(defaultFilters());
    setPage(1);
  }

  function goTo(next: number) {
    setState({ status: "loading" });
    setPage(next);
  }

  // A company can appear in several rows: keep every row's Watch state in step
  function setWatching(companyId: number, watching: boolean) {
    setState((s) =>
      s.status !== "ready"
        ? s
        : {
            ...s,
            data: {
              ...s.data,
              items: s.data.items.map((e) => (e.company_id === companyId ? { ...e, watching } : e)),
            },
          },
    );
  }

  async function exportCsv() {
    setExporting(true);
    setNotice(null);
    try {
      await download(`/events.csv?${query}`, `${industryId}-events.csv`);
    } catch (e) {
      setNotice(e instanceof ApiError ? e.message : "We couldn't export the CSV. Please try again.");
    } finally {
      setExporting(false);
    }
  }

  const defaults = defaultFilters();
  const filtered =
    filters.q !== "" ||
    filters.type !== "" ||
    filters.segment !== "" ||
    filters.from !== defaults.from ||
    filters.to !== "" ||
    filters.focusOnly;
  const total = state.status === "ready" ? state.data.total : null;

  return (
    <div className="flex flex-col gap-6">
      <IndustryTabs />

      <header className="flex flex-wrap items-end justify-between gap-6">
        <div className="flex flex-col gap-1.5">
          <span className="text-small font-medium text-teal">{industryName}</span>
          <h1 className="text-h1">Event tracker</h1>
          <span className="text-body text-muted" aria-live="polite">
            {total === null
              ? " "
              : `${total.toLocaleString("en-US")} ${total === 1 ? "event" : "events"}${filtered ? " match these filters" : ` in the last ${DEFAULT_DAYS} days`}`}
          </span>
        </div>
        <Button onClick={exportCsv} disabled={exporting || total === 0}>
          <Download size={16} strokeWidth={1.75} aria-hidden />
          {exporting ? "Exporting…" : "Export CSV"}
        </Button>
      </header>

      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-2 items-end gap-3 desk:grid-cols-[2fr_repeat(4,minmax(0,1fr))]">
          <Field label="Search" htmlFor="q" className="col-span-2 desk:col-span-1">
            <div className="relative">
              <Search
                size={16}
                strokeWidth={1.75}
                className="pointer-events-none absolute top-3 left-3 text-muted"
                aria-hidden
              />
              <input
                id="q"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Companies or keywords"
                className={`${FIELD} pl-9`}
              />
            </div>
          </Field>
          <Field label="Event type" htmlFor="type">
            <select id="type" value={filters.type} onChange={(e) => update({ type: e.target.value })} className={FIELD}>
              <option value="">All types</option>
              {options.types.map((t) => (
                <option key={t.value} value={t.value}>
                  {eventTypeLabel(t.value)} ({t.count})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Segment" htmlFor="segment">
            <select
              id="segment"
              value={filters.segment}
              onChange={(e) => update({ segment: e.target.value })}
              className={FIELD}
            >
              <option value="">All segments</option>
              {options.segments.map((s) => (
                <option key={s.value} value={s.value}>
                  {segmentLabel(s.value)} ({s.count})
                </option>
              ))}
            </select>
          </Field>
          <Field label="From" htmlFor="from">
            <input
              id="from"
              type="date"
              value={filters.from}
              max={filters.to || undefined}
              onChange={(e) => update({ from: e.target.value })}
              className={FIELD}
            />
          </Field>
          <Field label="To" htmlFor="to">
            <input
              id="to"
              type="date"
              value={filters.to}
              min={filters.from || undefined}
              onChange={(e) => update({ to: e.target.value })}
              className={FIELD}
            />
          </Field>
        </div>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          {focus && (
            <label className="flex min-h-10 cursor-pointer items-center gap-2 text-body">
              <input
                type="checkbox"
                checked={filters.focusOnly}
                onChange={(e) => update({ focusOnly: e.target.checked })}
                className="h-4 w-4"
              />
              Only my focus: {focus}
            </label>
          )}
          {filtered && (
            <Button variant="text" size="inline" className="h-10 text-small" onClick={clearFilters}>
              Clear filters
            </Button>
          )}
        </div>
      </div>

      {notice && (
        <p role="alert" className="rounded-base bg-danger-tint px-3 py-2.5 text-small text-danger">
          {notice}
        </p>
      )}

      {state.status === "error" ? (
        <div role="alert" className="flex flex-col items-start gap-3">
          <p className="text-body-lg text-ink">{state.message}</p>
          <Button
            onClick={() => {
              setState({ status: "loading" });
              setAttempt((n) => n + 1);
            }}
          >
            Try again
          </Button>
        </div>
      ) : state.status === "ready" && state.data.total === 0 ? (
        <div className="flex flex-col items-start gap-3 rounded-lg border border-border px-6 py-8">
          {filtered ? (
            <>
              <p className="text-body-lg text-ink">No events match these filters.</p>
              <Button onClick={clearFilters}>Clear filters</Button>
            </>
          ) : (
            <p className="text-body-lg text-ink">
              No events tracked for {industryName} in the last {DEFAULT_DAYS} days. New events arrive with each daily
              run.
            </p>
          )}
        </div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full min-w-[820px] border-collapse text-body">
              <caption className="sr-only">Events for {industryName}</caption>
              <thead>
                <tr className="bg-surface text-left">
                  <Th className="w-24">Date</Th>
                  <Th>Event</Th>
                  <Th className="w-56">Company</Th>
                  <Th className="w-48">Source</Th>
                </tr>
              </thead>
              <tbody aria-busy={state.status === "loading"}>
                {state.status === "loading"
                  ? Array.from({ length: 6 }, (_, i) => <SkeletonRow key={i} />)
                  : state.data.items.map((e) => (
                      <EventRow
                        key={e.id}
                        event={e}
                        onWatch={setWatching}
                        onError={setNotice}
                      />
                    ))}
              </tbody>
            </table>
          </div>

          {state.status === "ready" && (
            <Pagination page={page} total={state.data.total} onPage={goTo} />
          )}
        </>
      )}
    </div>
  );
}

const FIELD =
  "h-10 w-full rounded-base border border-border-input bg-white px-2.5 text-body text-ink placeholder:text-placeholder";

function Field({
  label,
  htmlFor,
  className,
  children,
}: {
  label: string;
  htmlFor: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={`flex min-w-0 flex-col gap-1.5 ${className ?? ""}`}>
      <label htmlFor={htmlFor} className="text-label text-muted">
        {label}
      </label>
      {children}
    </div>
  );
}

function Th({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <th
      scope="col"
      className={`border-b border-border px-4 py-3 text-label font-medium whitespace-nowrap text-muted ${className ?? ""}`}
    >
      {children}
    </th>
  );
}

function EventRow({
  event: e,
  onWatch,
  onError,
}: {
  event: EventItem;
  onWatch: (companyId: number, watching: boolean) => void;
  onError: (message: string) => void;
}) {
  return (
    <tr className="align-top">
      <td className="border-b border-border-row p-4 font-mono text-small whitespace-nowrap text-ink-3">
        {e.date ? <time dateTime={e.date}>{shortDate(e.date)}</time> : <span className="text-muted">No date</span>}
        {e.date && parseDate(e.date).getFullYear() !== new Date().getFullYear() && (
          <span className="block text-label text-muted">{parseDate(e.date).getFullYear()}</span>
        )}
      </td>
      <td className="border-b border-border-row p-4">
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-chip px-2.5 py-0.5 text-label whitespace-nowrap text-ink-2">
              {eventTypeLabel(e.event_type)}
            </span>
            {e.matches_focus && (
              <span className="rounded-full bg-amber-tint px-2.5 py-0.5 text-label font-semibold text-amber-text">
                Your focus
              </span>
            )}
          </div>
          <span className="text-body-lg text-ink">{e.summary}</span>
        </div>
      </td>
      <td className="border-b border-border-row p-4">
        <div className="flex flex-col items-start gap-2">
          <span className="leading-[1.45] font-medium">{e.company ?? <span className="text-muted">—</span>}</span>
          {e.company_id !== null && (
            <WatchButton
              companyId={e.company_id}
              companyName={e.company ?? "this company"}
              watching={e.watching}
              onChange={onWatch}
              onError={onError}
            />
          )}
        </div>
      </td>
      <td className="border-b border-border-row p-4">
        <SourceLink href={e.source_url} className="min-h-7">
          {e.source_name}
        </SourceLink>
      </td>
    </tr>
  );
}

function SkeletonRow() {
  return (
    <tr>
      {[16, 70, 40, 30].map((w, i) => (
        <td key={i} className="border-b border-border-row p-4">
          <div className="h-4 rounded-base bg-surface" style={{ width: `${w}%` }} />
        </td>
      ))}
    </tr>
  );
}

function Pagination({ page, total, onPage }: { page: number; total: number; onPage: (page: number) => void }) {
  const first = (page - 1) * PAGE_SIZE + 1;
  const last = Math.min(page * PAGE_SIZE, total);
  return (
    <nav aria-label="Pages" className="flex flex-wrap items-center justify-between gap-4 text-small text-muted">
      <span>
        Showing {first.toLocaleString("en-US")}–{last.toLocaleString("en-US")} of {total.toLocaleString("en-US")}
      </span>
      <div className="flex gap-2">
        <Button className="h-9 px-3.5 text-small" disabled={page === 1} onClick={() => onPage(page - 1)}>
          Previous
        </Button>
        <Button className="h-9 px-3.5 text-small" disabled={last >= total} onClick={() => onPage(page + 1)}>
          Next
        </Button>
      </div>
    </nav>
  );
}
