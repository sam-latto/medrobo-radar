import { createClient } from "@/lib/supabase/client";

/**
 * The only way the front end reads or writes data: the FastAPI service, with the Supabase
 * access token as a bearer token. Errors come back in one shape (api/errors.py):
 * {"error": {"code", "message", ...}}.
 */
const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public retryAfter?: string,
  ) {
    super(message);
  }
}

const FALLBACK_MESSAGE = "Something went wrong. Please try again.";

async function accessToken(): Promise<string | null> {
  const { data } = await createClient().auth.getSession();
  return data.session?.access_token ?? null;
}

// Signing out uses a full page load on purpose, so no signed-in state survives in memory.
export async function signOut(): Promise<void> {
  await createClient().auth.signOut();
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.assign("/sign-in");
}

type Options = { method?: string; body?: unknown; signal?: AbortSignal };

export async function api<T>(path: string, { method = "GET", body, signal }: Options = {}): Promise<T> {
  const token = await accessToken();
  if (!token) {
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/sign-in");
    throw new ApiError(401, "not_signed_in", "Please sign in.");
  }

  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method,
      signal,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network_error", "We couldn't reach Industry Radar. Check your connection and try again.");
  }

  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = data?.error ?? {};
    if (res.status === 401) {
      // Expired or invalid session: start over at sign-in
      await createClient().auth.signOut();
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign("/sign-in");
    }
    throw new ApiError(res.status, err.code ?? "http_error", err.message ?? FALLBACK_MESSAGE, err.retry_after);
  }
  return data as T;
}

/* Response shapes (api/routes/*.py) */

export type Subscription = { industry_id: string; name: string; focus: string | null };

export type Me = {
  id: string;
  email: string;
  display_name: string | null;
  email_brief_opt_in: boolean;
  onboarded_at: string | null;
  community: string;
  community_name: string;
  subscriptions: Subscription[];
  usage: unknown;
};

export type CatalogNode = {
  id: string;
  name: string;
  kind: "sector" | "industry";
  followable: boolean;
  tracking_active: boolean;
  industries?: CatalogNode[];
};

export type IndustryRequestResult = {
  label: string;
  status: "focus" | "pending_review";
  matched_industry_id: string | null;
  message: string;
};

export type EventItem = {
  id: number;
  date: string | null;
  industry_id: string;
  company: string | null;
  company_id: number | null;
  product: string | null;
  event_type: string;
  segment: string | null;
  summary: string;
  source_url: string;
  source_type: string;
  source_name: string;
  funding_amount: number | null;
  regulatory_status: string | null;
  watching: boolean;
  matches_focus: boolean;
};

export type Paged<T> = { items: T[]; total: number; page: number; page_size: number };

export type FocusEvent = Pick<
  EventItem,
  "id" | "date" | "company" | "product" | "segment" | "event_type" | "summary" | "source_url" | "source_type"
>;

export type Brief = {
  id: string;
  industry_id: string;
  industry_name: string;
  brief_date: string;
  headline: string | null;
  content_md: string;
  created_at: string;
  focus: string | null;
  focus_events: FocusEvent[];
};
