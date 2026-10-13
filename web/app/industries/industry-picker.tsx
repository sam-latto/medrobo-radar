"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { ChevronRight, Search, X } from "lucide-react";
import { api, ApiError, type CatalogNode, type IndustryRequestResult, type Me } from "@/lib/api";
import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const MAX_INDUSTRIES = 25; // api/routes/industries.py MAX_SUBSCRIPTIONS

/** Followed industry id → custom focus inside it (or null). */
type Selection = Map<string, string | null>;

type Loaded = { catalog: CatalogNode[]; alreadyOnboarded: boolean };

export function IndustryPicker() {
  const router = useRouter();
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Selection>(new Map());
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  async function load() {
    setLoadError(null);
    try {
      const [{ sectors }, me] = await Promise.all([
        api<{ sectors: CatalogNode[] }>("/industries"),
        api<Me>("/me"),
      ]);
      setSelected(new Map(me.subscriptions.map((s) => [s.industry_id, s.focus])));
      // Open the sectors the user already follows something in
      const followed = new Set(me.subscriptions.map((s) => s.industry_id));
      setOpen(new Set(sectors.filter((s) => s.industries?.some((i) => followed.has(i.id))).map((s) => s.id)));
      setLoaded({ catalog: sectors, alreadyOnboarded: me.subscriptions.length > 0 });
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : "We couldn't load the industry list. Please try again.");
    }
  }

  useEffect(() => {
    // Fetch the catalog and current subscriptions once on mount
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, []);

  const names = useMemo(() => {
    const out = new Map<string, string>();
    for (const node of loaded?.catalog ?? []) {
      out.set(node.id, node.name);
      for (const child of node.industries ?? []) out.set(child.id, child.name);
    }
    return out;
  }, [loaded]);

  function toggle(ids: string[], on: boolean) {
    setSelected((prev) => {
      const next = new Map(prev);
      for (const id of ids) {
        if (on) {
          if (!next.has(id)) next.set(id, null);
        } else next.delete(id);
      }
      return next;
    });
  }

  async function addCustom(e: FormEvent) {
    e.preventDefault();
    const label = draft.trim();
    if (label.length < 2) return;
    setAdding(true);
    try {
      const res = await api<IndustryRequestResult>("/industries/requests", { method: "POST", body: { label } });
      setNote(res.message);
      setDraft("");
      const matched = res.matched_industry_id;
      if (matched) {
        // The API already follows it with this focus; mirror that here (keeping an existing focus)
        setSelected((prev) => new Map(prev).set(matched, prev.get(matched) ?? res.label));
        const sector = loaded?.catalog.find((s) => s.industries?.some((i) => i.id === matched));
        if (sector) setOpen((prev) => new Set(prev).add(sector.id));
      }
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : "We couldn't add that. Please try again.");
    } finally {
      setAdding(false);
    }
  }

  async function save() {
    setSaving(true);
    setSaveError(null);
    try {
      await api("/subscriptions", {
        method: "PUT",
        body: { industries: [...selected].map(([industry_id, focus]) => ({ industry_id, focus })) },
      });
      router.push("/briefing");
    } catch (e) {
      setSaveError(e instanceof ApiError ? e.message : "We couldn't save your industries. Please try again.");
      setSaving(false);
    }
  }

  const q = query.trim().toLowerCase();
  const matches = (text: string) => text.toLowerCase().includes(q);
  const groups = (loaded?.catalog ?? [])
    .map((node) => {
      const children = node.industries ?? [];
      const visible = q && !matches(node.name) ? children.filter((c) => matches(c.name)) : children;
      return { node, visible, show: !q || matches(node.name) || visible.length > 0 };
    })
    .filter((g) => g.show);

  const count = selected.size;
  const overLimit = count > MAX_INDUSTRIES;

  return (
    <div className="flex min-h-screen flex-col items-center gap-7 bg-surface px-4 pt-6 pb-12 desk:px-6 desk:pt-10 desk:pb-16">
      <div className="flex w-full max-w-[680px] items-center justify-between">
        <Logo />
        {loaded?.alreadyOnboarded && (
          <Link href="/briefing" className="text-small font-medium">
            Back to briefing
          </Link>
        )}
      </div>

      <div className="flex w-full max-w-[680px] flex-col gap-[22px] rounded-xl border border-border bg-white p-5 desk:p-8">
        <div className="flex flex-col gap-2">
          <h1 className="text-h2 desk:text-[28px]">Which industries do you follow?</h1>
          <p className="text-body-lg text-muted">
            Pick a whole sector or narrow it down. You&apos;ll get a daily brief for each, and you can change this any
            time.
          </p>
        </div>

        {loadError ? (
          <div role="alert" className="flex flex-col items-start gap-3">
            <p className="text-body text-ink">{loadError}</p>
            <Button onClick={load}>Try again</Button>
          </div>
        ) : !loaded ? (
          <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading industries">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="h-11 rounded-base bg-surface" />
            ))}
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-2">
              <span className="text-label text-muted">Following</span>
              <div className="flex min-h-11 flex-wrap items-center gap-1.5 rounded-md border border-teal px-2.5 py-2">
                {count === 0 && <span className="text-body text-placeholder">Nothing selected yet</span>}
                {[...selected].map(([id, focus]) => {
                  const label = focus ? `${names.get(id) ?? id} · ${focus}` : (names.get(id) ?? id);
                  return (
                    <span
                      key={id}
                      className={cn(
                        "inline-flex h-7 items-center gap-1.5 rounded-full pr-1 pl-2.5 text-small font-medium text-teal-dark",
                        focus ? "bg-amber-tint" : "bg-teal-tint",
                      )}
                    >
                      {label}
                      <button
                        type="button"
                        onClick={() => toggle([id], false)}
                        aria-label={`Stop following ${label}`}
                        className="inline-flex h-[22px] w-[22px] cursor-pointer items-center justify-center rounded-full text-teal-dark hover:bg-white"
                      >
                        <X size={12} strokeWidth={2.25} aria-hidden />
                      </button>
                    </span>
                  );
                })}
              </div>
            </div>

            <div className="flex flex-col overflow-hidden rounded-lg border border-border">
              <div className="relative border-b border-border-row p-2.5">
                <Search
                  size={16}
                  strokeWidth={1.75}
                  className="pointer-events-none absolute top-[22px] left-[22px] text-muted"
                  aria-hidden
                />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search industries"
                  aria-label="Search industries"
                  className="h-10 w-full rounded-base border border-border-input pr-3 pl-9 text-body text-ink placeholder:text-placeholder"
                />
              </div>

              <div className="flex flex-col p-1.5">
                {groups.map(({ node, visible }) => (
                  <Group
                    key={node.id}
                    node={node}
                    visible={visible}
                    open={Boolean(q) || open.has(node.id)}
                    selected={selected}
                    onOpen={() =>
                      setOpen((prev) => {
                        const next = new Set(prev);
                        if (next.has(node.id)) next.delete(node.id);
                        else next.add(node.id);
                        return next;
                      })
                    }
                    onToggle={toggle}
                  />
                ))}
                {q && groups.length === 0 && (
                  <span className="px-3 py-2.5 text-body text-muted">
                    No industry matches &ldquo;{query}&rdquo;. Add it below.
                  </span>
                )}
              </div>

              <form onSubmit={addCustom} className="flex flex-col gap-2 border-t border-border-row bg-surface-subtle p-3.5">
                <label htmlFor="add-industry" className="text-small font-semibold">
                  Don&apos;t see it? Add a more specific industry
                </label>
                <div className="flex gap-2">
                  <input
                    id="add-industry"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    placeholder="e.g. Surgical robotics"
                    maxLength={120}
                    className="h-10 min-w-0 flex-1 rounded-base border border-border-input bg-white px-3 text-body text-ink placeholder:text-placeholder"
                  />
                  <Button type="submit" disabled={adding || draft.trim().length < 2}>
                    {adding ? "Adding…" : "Add"}
                  </Button>
                </div>
                {note && (
                  <p role="status" className="text-small text-teal-dark">
                    {note}
                  </p>
                )}
              </form>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className={cn("text-small", overLimit ? "text-warning" : "text-muted")}>
                {overLimit
                  ? `You can follow up to ${MAX_INDUSTRIES} industries. Remove ${count - MAX_INDUSTRIES} to continue.`
                  : count === 0
                    ? "Pick at least one to continue"
                    : `Following ${count} ${count === 1 ? "industry" : "industries"}`}
              </span>
              <Button variant="main" size="lg" onClick={save} disabled={saving || count === 0 || overLimit}>
                {saving ? "Saving…" : "Continue"}
              </Button>
            </div>
            {saveError && (
              <p role="alert" className="text-small text-danger">
                {saveError}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Group({
  node,
  visible,
  open,
  selected,
  onOpen,
  onToggle,
}: {
  node: CatalogNode;
  visible: CatalogNode[];
  open: boolean;
  selected: Selection;
  onOpen: () => void;
  onToggle: (ids: string[], on: boolean) => void;
}) {
  const isSector = node.kind === "sector";
  const all = isSector ? (node.industries ?? []).filter((i) => i.followable).map((i) => i.id) : [node.id];
  const picked = all.filter((id) => selected.has(id)).length;
  const checked = all.length > 0 && picked === all.length;
  const hasChildren = isSector && visible.length > 0;
  const countText = !isSector ? "" : picked === 0 ? `${all.length} areas` : checked ? "All" : `${picked} of ${all.length}`;
  const checkboxId = `ind-${node.id}`;

  return (
    <div className="flex flex-col">
      <div className="flex min-h-11 items-center gap-1 rounded-base px-1.5">
        {hasChildren ? (
          <button
            type="button"
            onClick={onOpen}
            aria-expanded={open}
            aria-label={`${open ? "Collapse" : "Expand"} ${node.name}`}
            className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-sm text-muted hover:bg-surface"
          >
            <ChevronRight size={16} strokeWidth={2} className={cn(open && "rotate-90")} aria-hidden />
          </button>
        ) : (
          <span className="w-8 shrink-0" />
        )}
        <input
          id={checkboxId}
          type="checkbox"
          checked={checked}
          ref={(el) => {
            if (el) el.indeterminate = picked > 0 && !checked;
          }}
          disabled={all.length === 0}
          onChange={() => onToggle(all, !checked)}
          className="h-[18px] w-[18px] cursor-pointer"
        />
        <label htmlFor={checkboxId} className="flex-1 cursor-pointer pl-2.5 text-body-lg font-semibold">
          {node.name}
        </label>
        {!isSector && !node.tracking_active && <NotTrackedYet />}
        <span className="text-label font-normal text-muted">{countText}</span>
      </div>

      {hasChildren && open && (
        <ul className="flex flex-col pb-1.5 pl-[46px]">
          {visible.map((child) => (
            <li key={child.id}>
              <label className="flex min-h-[38px] cursor-pointer items-center gap-2.5 rounded-base px-2 hover:bg-surface">
                <input
                  type="checkbox"
                  checked={selected.has(child.id)}
                  onChange={(e) => onToggle([child.id], e.target.checked)}
                  className="h-4 w-4"
                />
                <span className="flex-1 text-body text-ink">{child.name}</span>
                {!child.tracking_active && <NotTrackedYet />}
              </label>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Followable, but no pipeline profile is active yet, so no brief until it's reviewed. */
function NotTrackedYet() {
  return <span className="text-label font-normal text-muted">Briefs start soon</span>;
}
