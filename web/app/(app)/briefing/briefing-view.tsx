"use client";

import Link from "next/link";
import { Fragment, useEffect, useMemo, useState } from "react";
import { Printer } from "lucide-react";
import { api, ApiError, type Brief, type EventItem, type FocusEvent, type Paged } from "@/lib/api";
import { parseBrief, type Block, type Inline } from "@/lib/brief";
import { longDate, mediumDate, money, parseDate, shortDate } from "@/lib/format";
import { useSession } from "@/components/app-shell";
import { IndustryTabs } from "@/components/industry-tabs";
import { SourceLink } from "@/components/source-link";
import { Button } from "@/components/ui/button";

type Trackers = { deals: EventItem[]; regulatory: EventItem[] };

/** The side trackers show events from this many days up to the brief's date. */
const TRACKER_DAYS = 90;

function addDays(isoDate: string, days: number): string {
  const d = parseDate(isoDate);
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** The same story is often collected from several outlets: keep one row per company and date. */
function dedupe(events: EventItem[]): EventItem[] {
  const seen = new Set<string>();
  return events.filter((e) => {
    const key = `${(e.company ?? e.product ?? "").toLowerCase()}|${e.date ?? ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

type State =
  | { status: "loading" }
  | { status: "empty" }
  | { status: "error"; message: string }
  | { status: "ready"; brief: Brief };

/** Remounted (via `key`) whenever the sidebar's industry changes. */
export function BriefingView() {
  const { me, industryId } = useSession();
  const industryName = me.subscriptions.find((s) => s.industry_id === industryId)?.name ?? "";
  const [date, setDate] = useState<string | null>(null); // null = latest
  const [state, setState] = useState<State>({ status: "loading" });
  const [dates, setDates] = useState<string[]>([]);
  const [trackers, setTrackers] = useState<Trackers | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    const query = new URLSearchParams({ industry_id: industryId, ...(date ? { date } : {}) });
    api<Brief>(`/briefs?${query}`, { signal: ctrl.signal })
      .then((brief) => setState({ status: "ready", brief }))
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        if (e instanceof ApiError && e.status === 404) setState({ status: "empty" });
        else setState({ status: "error", message: e instanceof ApiError ? e.message : "We couldn't load this brief." });
      });
    return () => ctrl.abort();
  }, [industryId, date, attempt]);

  useEffect(() => {
    const ctrl = new AbortController();
    api<{ dates: string[] }>(`/briefs/dates?industry_id=${encodeURIComponent(industryId)}`, { signal: ctrl.signal })
      .then((r) => setDates(r.dates))
      .catch(() => {});
    return () => ctrl.abort();
  }, [industryId]);

  const briefDate = state.status === "ready" ? state.brief.brief_date : null;
  useEffect(() => {
    if (!briefDate) return;
    const ctrl = new AbortController();
    const signal = ctrl.signal;
    const range = `&from=${addDays(briefDate, -TRACKER_DAYS)}&to=${briefDate}`;
    const recent = (type: string) =>
      api<Paged<EventItem>>(
        `/events?industry_id=${encodeURIComponent(industryId)}&type=${type}&page_size=10${range}`,
        { signal },
      ).then((r) => r.items);
    Promise.all([recent("funding"), recent("partnership_ma"), recent("regulatory")])
      .then(([funding, deals, regulatory]) =>
        setTrackers({
          deals: dedupe([...funding, ...deals].sort((a, b) => (b.date ?? "").localeCompare(a.date ?? ""))).slice(0, 5),
          regulatory: dedupe(regulatory).slice(0, 5),
        }),
      )
      .catch(() => {
        if (!signal.aborted) setTrackers({ deals: [], regulatory: [] });
      });
    return () => ctrl.abort();
  }, [industryId, briefDate]);

  function pickDate(value: string) {
    setState({ status: "loading" });
    setDate(value === dates[0] ? null : value);
  }

  const shownDate = state.status === "ready" ? state.brief.brief_date : (date ?? dates[0] ?? null);

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        {/* Header strip */}
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex flex-wrap items-center gap-2.5 text-small text-muted">
            <span className="text-overline uppercase text-teal">{industryName} daily brief</span>
            {shownDate && (
              <>
                <span aria-hidden>·</span>
                <span>{longDate(shownDate)}</span>
              </>
            )}
          </div>
          <div className="flex items-center gap-2 print:hidden">
            {dates.length > 0 && (
              <>
                <label htmlFor="brief-date" className="sr-only">
                  Brief date
                </label>
                <select
                  id="brief-date"
                  value={shownDate ?? ""}
                  onChange={(e) => pickDate(e.target.value)}
                  disabled={dates.length < 2}
                  className="h-9 rounded-base border border-border-input bg-white px-2.5 text-small text-ink disabled:opacity-100"
                >
                  {dates.map((d) => (
                    <option key={d} value={d}>
                      {mediumDate(d)}
                    </option>
                  ))}
                </select>
              </>
            )}
            {state.status === "ready" && (
              <Button className="h-9 px-3 text-small" onClick={() => window.print()}>
                <Printer size={14} strokeWidth={1.75} aria-hidden />
                PDF
              </Button>
            )}
          </div>
        </div>
        <IndustryTabs />
      </div>

      {state.status === "loading" && <BriefSkeleton />}
      {state.status === "empty" && (
        <div className="flex max-w-[680px] flex-col items-start gap-3">
          <h1 className="text-h2">No brief yet for {industryName}</h1>
          <p className="text-body-lg text-muted">
            Briefs are written each morning from the day&apos;s new filings, research and news. The first one for this
            industry arrives after its next daily run.
          </p>
          <Link href="/events" className="text-body font-medium">
            See tracked events
          </Link>
        </div>
      )}
      {state.status === "error" && (
        <div role="alert" className="flex max-w-[680px] flex-col items-start gap-3">
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
      )}
      {state.status === "ready" && (
        <BriefBody brief={state.brief} industryName={industryName} trackers={trackers} />
      )}
    </div>
  );
}

function BriefBody({
  brief,
  industryName,
  trackers,
}: {
  brief: Brief;
  industryName: string;
  trackers: Trackers | null;
}) {
  const doc = useMemo(() => parseBrief(brief.content_md), [brief.content_md]);
  // Every brief gets a headline, so they all open the same way
  const headline = doc.headline ?? `Today in ${industryName.toLowerCase()}`;
  const minutes = Math.max(1, Math.round(doc.words / 230));

  return (
    <>
      <header className="flex max-w-[900px] flex-col gap-4">
        <h1 className="font-serif text-[36px] leading-[1.08] font-medium tracking-[-0.015em] desk:text-brief-headline">
          {headline}
        </h1>
        {doc.deck && <p className="font-serif text-brief-deck text-ink-3">{doc.deck}</p>}
        <div className="flex flex-wrap gap-3.5 text-small text-muted">
          <span>Industry Radar research</span>
          <span aria-hidden>·</span>
          <span>{minutes} min read</span>
          {doc.sources.length > 0 && (
            <>
              <span aria-hidden>·</span>
              <span>
                {doc.sources.length} {doc.sources.length === 1 ? "source" : "sources"}
              </span>
            </>
          )}
        </div>
      </header>

      {doc.numbers.length > 0 && (
        <div className="grid grid-cols-2 border-t-2 border-b border-t-ink border-b-border desk:grid-cols-4">
          {doc.numbers.slice(0, 4).map((f, i) => (
            <div
              key={i}
              className={
                "flex flex-col gap-1.5 py-[18px] " +
                (i % 2 === 1 ? "border-l border-border pl-5 " : "pr-5 ") +
                (i >= 2 ? "border-t border-border desk:border-t-0 " : "") +
                (i === 2 ? "desk:border-l desk:pl-5 " : "")
              }
            >
              <span className="font-serif text-brief-number font-medium">{f.figure}</span>
              <span className="text-small text-ink-3">
                <Rich nodes={f.caption} />
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 items-start gap-12 desk:grid-cols-[minmax(0,1fr)_340px] desk:gap-16">
        <article className="flex max-w-[700px] flex-col gap-[18px] font-serif text-[17px] leading-[1.65] text-ink-body desk:text-brief-body">
          {doc.blocks.map((block, i) => (
            <BlockView key={i} block={block} />
          ))}

          {doc.sources.length > 0 && (
            <section
              aria-labelledby="sources-heading"
              className="mt-5 flex flex-col gap-2 border-t border-border pt-4 font-sans text-small text-muted"
            >
              <h2 id="sources-heading" className="text-overline uppercase text-ink">
                Sources
              </h2>
              <ol className="flex list-decimal flex-col gap-1 pl-5">
                {doc.sources.map((s) => (
                  <li key={s.n} id={`source-${s.n}`} className="scroll-mt-6">
                    <SourceLink href={s.url}>{s.label}</SourceLink>
                    {s.host && s.host !== s.label.toLowerCase() && <span> · {s.host}</span>}
                  </li>
                ))}
              </ol>
            </section>
          )}
        </article>

        <aside className="flex flex-col gap-7 print:hidden">
          {brief.focus && <FocusList focus={brief.focus} events={brief.focus_events} />}
          {trackers === null ? (
            <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading trackers">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="h-10 rounded-base bg-surface" />
              ))}
            </div>
          ) : (
            <>
              <Tracker title="Deal tracker" events={trackers.deals} right={(e) => money(e.funding_amount) ?? "n/d"} />
              <Tracker
                title="Regulatory tracker"
                events={trackers.regulatory}
                right={(e) => e.regulatory_status ?? "Filed"}
              />
            </>
          )}
          <Link
            href="/events"
            className="flex min-h-11 items-center justify-between rounded-md border border-border px-4 text-small font-medium no-underline"
          >
            All events <span aria-hidden>→</span>
          </Link>
        </aside>
      </div>
    </>
  );
}

function BlockView({ block }: { block: Block }) {
  switch (block.type) {
    case "callout":
      return (
        <section className="flex flex-col gap-3 rounded-lg bg-surface px-6 py-[22px] font-sans">
          <h2 className="flex items-center gap-2 text-[13px] font-semibold tracking-[0.06em] text-ink uppercase">
            <span className="h-2 w-2 rounded-[2px] bg-amber" aria-hidden />
            {block.title}
          </h2>
          <ul className="flex list-disc flex-col gap-2 pl-[18px] text-body-lg leading-[1.6] text-ink-body">
            {block.items.map((item, i) => (
              <li key={i}>
                <Rich nodes={item} />
              </li>
            ))}
          </ul>
        </section>
      );
    case "section":
      return (
        <>
          {block.label && (
            <span className="mt-3.5 font-sans text-overline uppercase text-teal">{block.label}</span>
          )}
          {block.title && (
            <h2 className="font-serif text-[26px] leading-[1.2] font-medium text-ink desk:text-brief-h2">
              {block.title}
            </h2>
          )}
        </>
      );
    case "why":
      return (
        <div className="flex flex-col gap-1.5 border-y border-border py-4 font-sans">
          <span className="text-overline uppercase text-muted">Why it matters</span>
          <span className="text-[16px] leading-[1.6] text-ink-body">
            <Rich nodes={block.content} />
          </span>
        </div>
      );
    case "p":
      // Bold inside serif paragraphs reads as shouting: medium weight there
      return (
        <p>
          <Rich nodes={block.content} quietBold />
        </p>
      );
    case "ul":
      return (
        <ul className="flex list-disc flex-col gap-2 pl-[22px]">
          {block.items.map((item, i) => (
            <li key={i}>
              <Rich nodes={item} />
            </li>
          ))}
        </ul>
      );
    case "ol":
    case "watch":
      return (
        <ol className="flex list-decimal flex-col gap-2.5 pl-[22px]">
          {block.items.map((item, i) => (
            <li key={i}>
              <Rich nodes={item} />
            </li>
          ))}
        </ol>
      );
  }
}

function Rich({ nodes, quietBold = false }: { nodes: Inline[]; quietBold?: boolean }) {
  return (
    <>
      {nodes.map((n, i) => (
        <Fragment key={i}>
          {n.t === "text" ? (
            n.text
          ) : n.t === "b" ? (
            <strong className={quietBold ? "font-medium" : "font-semibold"}>
              <Rich nodes={n.children} quietBold={quietBold} />
            </strong>
          ) : n.t === "i" ? (
            <em>
              <Rich nodes={n.children} />
            </em>
          ) : (
            <sup>
              <a
                href={`#source-${n.n}`}
                aria-label={`Source ${n.n}`}
                className="px-0.5 font-mono text-[11px] no-underline"
              >
                {n.n}
              </a>
            </sup>
          )}
        </Fragment>
      ))}
    </>
  );
}

function SideHeading({ children }: { children: React.ReactNode }) {
  return <h2 className="border-b-2 border-ink pb-2 text-overline uppercase">{children}</h2>;
}

function Tracker({ title, events, right }: { title: string; events: EventItem[]; right: (e: EventItem) => string }) {
  if (events.length === 0) return null;
  return (
    <section className="flex flex-col gap-2.5">
      <SideHeading>{title}</SideHeading>
      <ul className="flex flex-col text-small">
        {events.map((e) => (
          <li key={e.id} className="flex justify-between gap-3 border-b border-border-row py-2.5 last:border-b-0">
            <div className="flex min-w-0 flex-col">
              <span className="font-semibold">{e.company ?? e.product ?? "Unnamed"}</span>
              <span className="text-muted">
                {e.date ? `${shortDate(e.date)} · ` : ""}
                <SourceLink href={e.source_url} className="text-small">
                  {e.source_name}
                </SourceLink>
              </span>
            </div>
            <span className="shrink-0 text-right font-mono text-ink-3">{right(e)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function FocusList({ focus, events }: { focus: string; events: FocusEvent[] }) {
  return (
    <section className="flex flex-col gap-2.5">
      <SideHeading>For your focus: {focus}</SideHeading>
      {events.length === 0 ? (
        <p className="text-small text-muted">Nothing on {focus} in this brief&apos;s run.</p>
      ) : (
        <ul className="flex flex-col text-small">
          {events.map((e) => (
            <li key={e.id} className="flex flex-col gap-1 border-b border-border-row py-2.5 last:border-b-0">
              <span>
                <span className="font-semibold">{e.company ?? "Unnamed"}</span> · {e.summary}
              </span>
              <SourceLink href={e.source_url} className="text-small">
                {e.date ? shortDate(e.date) : "Source"}
              </SourceLink>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function BriefSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-busy="true" aria-label="Loading brief">
      <div className="h-12 w-full max-w-[760px] rounded-base bg-surface" />
      <div className="h-6 w-full max-w-[620px] rounded-base bg-surface" />
      <div className="mt-6 h-24 w-full rounded-lg bg-surface" />
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="h-4 w-full max-w-[700px] rounded-base bg-surface" />
      ))}
    </div>
  );
}
