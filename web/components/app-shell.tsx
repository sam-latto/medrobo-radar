"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, use, useCallback, useEffect, useState, type ReactNode } from "react";
import { BriefcaseBusiness, Building2, List, Menu, Newspaper, X } from "lucide-react";
import { api, ApiError, signOut, type Me } from "@/lib/api";
import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Session = {
  me: Me;
  industryId: string;
  setIndustryId: (id: string) => void;
  reload: () => Promise<void>;
};

const SessionContext = createContext<Session | null>(null);

/** The signed-in user and the industry picked in the sidebar. Only inside the app shell. */
export function useSession(): Session {
  const session = use(SessionContext);
  if (!session) throw new Error("useSession must be used inside AppShell");
  return session;
}

const INDUSTRY_KEY = "industry-radar:industry";

function rememberedIndustry(): string | null {
  try {
    return localStorage.getItem(INDUSTRY_KEY);
  } catch {
    return null;
  }
}

const NAV = [
  { href: "/briefing", label: "Briefing", icon: Newspaper },
  { href: "/events", label: "Events", icon: List },
  { href: "/companies", label: "Companies", icon: Building2 },
  { href: "/jobs", label: "Jobs", icon: BriefcaseBusiness },
];

export function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [industryId, setIndustry] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const pathname = usePathname();

  const reload = useCallback(async () => {
    setError(null);
    try {
      const profile = await api<Me>("/me");
      if (profile.subscriptions.length === 0) {
        router.replace("/industries");
        return;
      }
      const ids = profile.subscriptions.map((s) => s.industry_id);
      const saved = rememberedIndustry();
      setIndustry((current) => (ids.includes(current) ? current : saved && ids.includes(saved) ? saved : ids[0]));
      setMe(profile);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "unknown", "Something went wrong. Please try again."));
    }
  }, [router]);

  useEffect(() => {
    // Loading the profile once on mount is the point of this effect
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload();
  }, [reload]);

  // Close the phone menu after navigating (adjusting state during render, not in an effect)
  const [menuPath, setMenuPath] = useState(pathname);
  if (menuPath !== pathname) {
    setMenuPath(pathname);
    setMenuOpen(false);
  }

  const setIndustryId = useCallback((id: string) => {
    setIndustry(id);
    try {
      localStorage.setItem(INDUSTRY_KEY, id);
    } catch {}
  }, []);

  if (error) return <ShellError error={error} onRetry={reload} />;
  if (!me) return <ShellLoading />;

  const sidebar = <Sidebar me={me} pathname={pathname} />;

  return (
    <SessionContext value={{ me, industryId, setIndustryId, reload }}>
      <div className="flex min-h-screen flex-col desk:flex-row">
        {/* Phone: top bar with a menu button */}
        <header className="sticky top-0 z-20 flex h-14 print:hidden items-center justify-between border-b border-border bg-surface px-4 desk:hidden">
          <Logo />
          <Button
            variant="text"
            size="inline"
            className="h-10 w-10 text-ink"
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((o) => !o)}
          >
            {menuOpen ? <X size={20} aria-hidden /> : <Menu size={20} aria-hidden />}
          </Button>
        </header>
        {menuOpen && <div className="border-b border-border bg-surface desk:hidden">{sidebar}</div>}

        <aside className="sticky top-0 hidden h-screen print:hidden w-[248px] shrink-0 border-r border-border bg-surface desk:block">
          {sidebar}
        </aside>

        <main className="min-w-0 flex-1 px-4 pt-6 pb-16 desk:px-14 desk:pt-10 desk:pb-[72px] print:p-0">{children}</main>
      </div>
    </SessionContext>
  );
}

function Sidebar({ me, pathname }: { me: Me; pathname: string }) {
  const name = me.display_name || me.email;
  return (
    <div className="flex h-full flex-col gap-7 px-4 pt-6 pb-5">
      <Logo className="hidden px-2 desk:flex" />

      <nav aria-label="Main" className="flex flex-col gap-0.5">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex h-10 items-center gap-3 rounded-base px-3 text-body no-underline",
                active ? "bg-teal-tint font-semibold text-teal-dark hover:text-teal-dark" : "font-medium text-ink-3 hover:bg-white hover:text-ink",
              )}
            >
              <Icon size={18} strokeWidth={1.75} aria-hidden />
              <span>{label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="flex-1" />

      <div className="flex items-center gap-2.5 px-1">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-teal-tint text-label font-semibold text-teal-dark">
          {initials(name)}
        </span>
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-small font-medium">{name}</span>
          <span className="truncate text-label font-normal text-muted">{me.community_name}</span>
        </div>
        <Button variant="text" size="inline" className="h-10 px-1 text-label text-muted" onClick={signOut}>
          Sign out
        </Button>
      </div>
    </div>
  );
}

function initials(name: string): string {
  const words = name.split("@")[0].split(/[\s._-]+/).filter(Boolean);
  return ((words[0]?.[0] ?? "") + (words[1]?.[0] ?? "")).toUpperCase() || "?";
}

function ShellLoading() {
  return (
    <div className="flex min-h-screen" aria-busy="true" aria-label="Loading">
      <div className="hidden w-[248px] border-r border-border bg-surface desk:block" />
      <div className="flex flex-1 flex-col gap-4 px-4 pt-10 desk:px-14">
        <div className="h-8 w-56 rounded-base bg-surface" />
        <div className="h-4 w-full max-w-[600px] rounded-base bg-surface" />
        <div className="h-4 w-full max-w-[520px] rounded-base bg-surface" />
      </div>
    </div>
  );
}

function ShellError({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  const notAllowed = error.code === "account_not_allowed";
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div role="alert" className="flex max-w-[420px] flex-col items-start gap-4">
        <Logo />
        <p className="text-body-lg text-ink">{error.message}</p>
        <div className="flex gap-3">
          {notAllowed ? (
            <Button variant="main" onClick={signOut}>
              Use another account
            </Button>
          ) : (
            <>
              <Button variant="main" onClick={onRetry}>
                Try again
              </Button>
              <Button variant="text" onClick={signOut}>
                Sign out
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
