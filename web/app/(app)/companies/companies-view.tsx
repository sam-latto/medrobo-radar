"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { api, ApiError, type CompanyItem, type CompanyProfile } from "@/lib/api";
import { money, parseDate, shortDate } from "@/lib/format";
import { eventTypeLabel } from "@/lib/labels";
import { cn } from "@/lib/utils";
import { useSession } from "@/components/app-shell";
import { IndustryTabs } from "@/components/industry-tabs";
import { SourceLink } from "@/components/source-link";
import { WatchButton } from "@/components/watch-button";
import { Button } from "@/components/ui/button";

const PAGE = 50; // rows shown before "Show more"
const ACTIVITY_SHOWN = 8;

type ListState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; items: CompanyItem[] };

/** Remounted (via `key`) when the industry tab changes. */
export function CompaniesView() {
  const { me, industryId } = useSession();
  const industryName = me.subscriptions.find((s) => s.industry_id === industryId)?.name ?? "";

  const [list, setList] = useState<ListState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [search, setSearch] = useState("");
  const [onlyWatching, setOnlyWatching] = useState(false);
  const [shown, setShown] = useState(PAGE);
  const [selected, setSelected] = useState<number | null>(null);
  // Watch changes made on this page, so the table, profile and similar companies agree
  const [watchOverrides, setWatchOverrides] = useState<Map<number, boolean>>(new Map());
  const [notice, setNotice] = useState<string | null>(null);
  const profileRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    api<{ items: CompanyItem[] }>(`/companies?industry_id=${encodeURIComponent(industryId)}`, { signal: ctrl.signal })
      .then((r) => setList({ status: "ready", items: r.items }))
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        setList({ status: "error", message: e instanceof ApiError ? e.message : "We couldn't load companies." });
      });
    return () => ctrl.abort();
  }, [industryId, attempt]);

  const isWatching = useCallback(
    (id: number, fromApi: boolean) => watchOverrides.get(id) ?? fromApi,
    [watchOverrides],
  );

  const setWatching = useCallback((id: number, watching: boolean) => {
    setWatchOverrides((prev) => new Map(prev).set(id, watching));
  }, []);

  const rows = useMemo(() => {
    if (list.status !== "ready") return [];
    const needle = search.trim().toLowerCase();
    return list.items.filter(
      (c) =>
        (!needle || c.company.toLowerCase().includes(needle) || (c.description ?? "").toLowerCase().includes(needle)) &&
        (!onlyWatching || isWatching(c.company_id, c.watching)),
    );
  }, [list, search, onlyWatching, isWatching]);

  const watchedCount =
    list.status === "ready" ? list.items.filter((c) => isWatching(c.company_id, c.watching)).length : 0;

  function select(id: number) {
    setSelected(id);
    // Let the panel render, then bring it into view (it sits below the table)
    requestAnimationFrame(() => profileRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  return (
    <div className="flex flex-col gap-6">
      <IndustryTabs />

      <header className="flex flex-col gap-1.5">
        <span className="text-small font-medium text-teal">{industryName}</span>
        <h1 className="text-h1">Companies</h1>
        <span className="text-body text-muted">
          Ranked by recent activity: funding, regulatory actions, launches and news from trusted sources.
        </span>
      </header>

      <div className="flex flex-wrap items-end gap-3">
        <div className="flex min-w-0 flex-1 basis-64 flex-col gap-1.5">
          <label htmlFor="company-search" className="text-label text-muted">
            Search companies
          </label>
          <div className="relative">
            <Search size={16} strokeWidth={1.75} className="pointer-events-none absolute top-3 left-3 text-muted" aria-hidden />
            <input
              id="company-search"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setShown(PAGE);
              }}
              placeholder="Name or what they do"
              className="h-10 w-full rounded-base border border-border-input bg-white pr-3 pl-9 text-body text-ink placeholder:text-placeholder"
            />
          </div>
        </div>
        <div role="group" aria-label="Show" className="flex h-10 rounded-base border border-border-input p-0.5">
          {[
            { value: false, label: "All companies" },
            { value: true, label: `Watching (${watchedCount})` },
          ].map((o) => (
            <button
              key={o.label}
              type="button"
              aria-pressed={onlyWatching === o.value}
              onClick={() => {
                setOnlyWatching(o.value);
                setShown(PAGE);
              }}
              className={cn(
                "cursor-pointer rounded-sm px-3 text-small font-medium whitespace-nowrap",
                onlyWatching === o.value ? "bg-teal-tint text-teal-dark" : "text-ink-3 hover:text-ink",
              )}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {notice && (
        <p role="alert" className="rounded-base bg-danger-tint px-3 py-2.5 text-small text-danger">
          {notice}
        </p>
      )}

      {list.status === "error" ? (
        <div role="alert" className="flex flex-col items-start gap-3">
          <p className="text-body-lg text-ink">{list.message}</p>
          <Button
            onClick={() => {
              setList({ status: "loading" });
              setAttempt((n) => n + 1);
            }}
          >
            Try again
          </Button>
        </div>
      ) : list.status === "ready" && rows.length === 0 ? (
        <div className="flex flex-col items-start gap-3 rounded-lg border border-border px-6 py-8">
          <p className="text-body-lg text-ink">
            {onlyWatching && !search
              ? "You're not watching any companies here yet. Use Watch on a company to follow it."
              : search
                ? `No company matches "${search.trim()}".`
                : `No companies tracked for ${industryName} yet. They're added as they appear in trusted sources.`}
          </p>
          {(search || onlyWatching) && (
            <Button
              onClick={() => {
                setSearch("");
                setOnlyWatching(false);
              }}
            >
              Show all companies
            </Button>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="relative overflow-x-auto rounded-lg border border-border">
            <table className="w-full min-w-[760px] border-collapse text-body">
              <caption className="sr-only">Companies in {industryName}, most active first</caption>
              <thead>
                <tr className="bg-surface text-left">
                  <Th className="w-[30%]">Company</Th>
                  <Th>What they do</Th>
                  <Th className="w-40">Latest activity</Th>
                  <Th className="w-32">
                    <span className="sr-only">Watch</span>
                  </Th>
                </tr>
              </thead>
              <tbody aria-busy={list.status === "loading"}>
                {list.status === "loading"
                  ? Array.from({ length: 8 }, (_, i) => <SkeletonRow key={i} />)
                  : rows.slice(0, shown).map((c) => (
                      <tr
                        key={c.company_id}
                        className={cn("align-top", selected === c.company_id && "bg-teal-tint/40")}
                      >
                        <td className="border-b border-border-row p-4">
                          <div className="flex flex-col items-start gap-1">
                            <button
                              type="button"
                              onClick={() => select(c.company_id)}
                              aria-expanded={selected === c.company_id}
                              className="cursor-pointer text-left font-semibold text-ink hover:text-teal hover:underline"
                            >
                              {c.company}
                            </button>
                            {c.key_company && (
                              <span className="rounded-full bg-chip px-2 py-0.5 text-label text-ink-2">Key company</span>
                            )}
                          </div>
                        </td>
                        <td className="border-b border-border-row p-4 text-ink-3">
                          {c.description ??
                            (c.latest_summary ? (
                              <span>
                                <span className="text-muted">Latest: </span>
                                {c.latest_summary}
                              </span>
                            ) : (
                              <span className="text-placeholder">No description yet</span>
                            ))}
                        </td>
                        <td className="border-b border-border-row p-4">
                          {c.last_event ? (
                            <div className="flex flex-col">
                              <span className="font-mono text-small text-ink-3">{shortDate(c.last_event)}</span>
                              <span className="text-small text-muted">
                                {c.events_90d} {c.events_90d === 1 ? "event" : "events"} in 90 days
                              </span>
                            </div>
                          ) : (
                            <span className="text-small text-muted">Nothing in 90 days</span>
                          )}
                        </td>
                        <td className="border-b border-border-row p-4">
                          <WatchButton
                            companyId={c.company_id}
                            companyName={c.company}
                            watching={isWatching(c.company_id, c.watching)}
                            onChange={setWatching}
                            onError={setNotice}
                          />
                        </td>
                      </tr>
                    ))}
              </tbody>
            </table>
          </div>
          {list.status === "ready" && (
            <div className="flex flex-wrap items-center justify-between gap-3 text-small text-muted">
              <span>
                Showing {Math.min(shown, rows.length).toLocaleString("en-US")} of {rows.length.toLocaleString("en-US")}
              </span>
              {shown < rows.length && (
                <Button className="h-9 px-3.5 text-small" onClick={() => setShown((n) => n + PAGE)}>
                  Show more
                </Button>
              )}
            </div>
          )}
        </div>
      )}

      {selected !== null && (
        <section ref={profileRef} aria-label="Company profile" className="scroll-mt-6">
          <CompanyProfilePanel
            key={selected}
            companyId={selected}
            isWatching={isWatching}
            onWatch={setWatching}
            onError={setNotice}
            onSelect={select}
            onClose={() => setSelected(null)}
          />
        </section>
      )}
    </div>
  );
}

function Th({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <th
      scope="col"
      className={cn("border-b border-border px-4 py-3 text-label font-medium whitespace-nowrap text-muted", className)}
    >
      {children}
    </th>
  );
}

function SkeletonRow() {
  return (
    <tr>
      {[45, 80, 50, 40].map((w, i) => (
        <td key={i} className="border-b border-border-row p-4">
          <div className="h-4 rounded-base bg-surface" style={{ width: `${w}%` }} />
        </td>
      ))}
    </tr>
  );
}

type ProfileState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; company: CompanyProfile };

const YEAR_MS = 365 * 24 * 60 * 60 * 1000;

function withinYear(date: string | null): boolean {
  return date !== null && Date.now() - parseDate(date).getTime() <= YEAR_MS;
}

function CompanyProfilePanel({
  companyId,
  isWatching,
  onWatch,
  onError,
  onSelect,
  onClose,
}: {
  companyId: number;
  isWatching: (id: number, fromApi: boolean) => boolean;
  onWatch: (id: number, watching: boolean) => void;
  onError: (message: string) => void;
  onSelect: (id: number) => void;
  onClose: () => void;
}) {
  const [state, setState] = useState<ProfileState>({ status: "loading" });
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    api<CompanyProfile>(`/companies/${companyId}`, { signal: ctrl.signal })
      .then((company) => setState({ status: "ready", company }))
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        setState({ status: "error", message: e instanceof ApiError ? e.message : "We couldn't load this company." });
      });
    return () => ctrl.abort();
  }, [companyId]);

  if (state.status === "loading") {
    return (
      <div className="flex flex-col gap-3 rounded-lg border border-border p-6" aria-busy="true" aria-label="Loading company">
        <div className="h-6 w-56 rounded-base bg-surface" />
        <div className="h-4 w-full max-w-[600px] rounded-base bg-surface" />
        <div className="h-20 rounded-base bg-surface" />
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div role="alert" className="rounded-lg border border-border p-6 text-body-lg text-ink">
        {state.message}
      </div>
    );
  }

  const c = state.company;
  const funding = c.events
    .filter((e) => e.event_type === "funding" && e.funding_amount && withinYear(e.date))
    .reduce((sum, e) => sum + (e.funding_amount ?? 0), 0);
  const fdaActions = c.filings.filter((e) => e.source_type === "openfda" && withinYear(e.date)).length;
  const activity = showAll ? c.events : c.events.slice(0, ACTIVITY_SHOWN);
  const thin = c.events.length === 0 && !c.description;
  let website: string | null = null;
  try {
    if (c.website) website = new URL(c.website.startsWith("http") ? c.website : `https://${c.website}`).href;
  } catch {}

  return (
    <div className="flex flex-col gap-7 rounded-lg border border-border p-5 desk:p-7">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex max-w-[680px] min-w-0 flex-col gap-2">
          <h2 className="text-h2">{c.name}</h2>
          <div className="flex flex-wrap gap-1.5">
            {c.industries.map((i) => (
              <span key={i.id} className="rounded-full bg-chip px-2.5 py-0.5 text-label text-ink-2">
                {i.name}
              </span>
            ))}
          </div>
          {c.description && <p className="text-body-lg text-ink-3">{c.description}</p>}
          {website && (
            <SourceLink href={website} className="text-small">
              {new URL(website).hostname.replace(/^www\./, "")}
            </SourceLink>
          )}
        </div>
        <div className="flex items-center gap-2">
          <WatchButton
            companyId={c.id}
            companyName={c.name}
            watching={isWatching(c.id, c.watching)}
            onChange={onWatch}
            onError={onError}
          />
          <Button variant="text" size="inline" className="h-10 w-10 text-muted" aria-label="Close profile" onClick={onClose}>
            <X size={18} aria-hidden />
          </Button>
        </div>
      </div>

      {thin ? (
        <p className="text-body-lg text-muted">
          We don&apos;t have much on {c.name} yet. It will fill in as it appears in filings, research and trusted news.
        </p>
      ) : (
        <>
          <dl className="grid grid-cols-2 border-t-2 border-b border-t-ink border-b-border desk:grid-cols-4">
            <Stat label="Disclosed funding (12 mo)" value={money(funding) ?? "None disclosed"} />
            {fdaActions > 0 && <Stat label="FDA actions (12 mo)" value={String(fdaActions)} />}
            {c.trials.length > 0 && <Stat label="Clinical trials" value={String(c.trials.length)} />}
            <Stat label="Events tracked" value={String(c.events.length)} />
            <Stat label="Open roles" value={String(c.hiring.open_roles)} />
          </dl>

          <section className="flex flex-col gap-3">
            <h3 className="text-h3">Activity</h3>
            {c.events.length === 0 ? (
              <p className="text-body text-muted">No events tracked yet.</p>
            ) : (
              <ul className="flex flex-col">
                {activity.map((e) => (
                  <li
                    key={e.id}
                    className="grid grid-cols-[72px_minmax(0,1fr)] gap-x-4 gap-y-1 border-b border-border-row py-3 last:border-b-0 desk:grid-cols-[72px_150px_minmax(0,1fr)_auto]"
                  >
                    <span className="font-mono text-small text-ink-3">{e.date ? shortDate(e.date) : "No date"}</span>
                    <span>
                      <span className="rounded-full bg-chip px-2.5 py-0.5 text-label whitespace-nowrap text-ink-2">
                        {eventTypeLabel(e.event_type)}
                      </span>
                    </span>
                    <span className="col-span-2 text-body text-ink desk:col-span-1">{e.summary}</span>
                    <SourceLink href={e.source_url} className="col-span-2 text-small desk:col-span-1">
                      {e.source_name}
                    </SourceLink>
                  </li>
                ))}
              </ul>
            )}
            {c.events.length > ACTIVITY_SHOWN && (
              <Button variant="text" size="inline" className="h-10 self-start text-small" onClick={() => setShowAll((s) => !s)}>
                {showAll ? "Show less" : `Show all ${c.events.length} events`}
              </Button>
            )}
          </section>

          {c.hiring.postings.length > 0 && (
            <section className="flex flex-col gap-3">
              <h3 className="text-h3">Open roles</h3>
              <ul className="flex flex-col">
                {c.hiring.postings.map((p) => (
                  <li key={p.id} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-border-row py-2.5 last:border-b-0">
                    <span className="text-body text-ink">
                      {p.title}
                      {p.location && <span className="text-muted"> · {p.location}</span>}
                    </span>
                    <SourceLink href={p.url} className="text-small">
                      Open posting
                    </SourceLink>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}

      {c.similar.length > 0 && (
        <section className="flex flex-col gap-3">
          <h3 className="text-h3">Similar companies</h3>
          <ul className="grid grid-cols-1 gap-3 desk:grid-cols-3">
            {c.similar.slice(0, 3).map((s) => (
              <li key={s.id} className="flex flex-col gap-2 rounded-md border border-border p-4">
                <button
                  type="button"
                  onClick={() => onSelect(s.id)}
                  className="cursor-pointer self-start text-left font-semibold text-ink hover:text-teal hover:underline"
                >
                  {s.name}
                </button>
                <span className="text-small text-muted">
                  {s.events_90d} {s.events_90d === 1 ? "event" : "events"} in 90 days
                </span>
                {s.description && <span className="line-clamp-3 text-small text-ink-3">{s.description}</span>}
                <div className="mt-auto pt-1">
                  <WatchButton
                    companyId={s.id}
                    companyName={s.name}
                    watching={isWatching(s.id, s.watching)}
                    onChange={onWatch}
                    onError={onError}
                  />
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1.5 border-border py-4 pr-4 [&:nth-child(even)]:border-l [&:nth-child(even)]:pl-4 desk:[&:not(:first-child)]:border-l desk:[&:not(:first-child)]:pl-4">
      <dt className="text-small text-muted">{label}</dt>
      <dd className="font-serif text-[28px] leading-none font-medium text-ink">{value}</dd>
    </div>
  );
}
