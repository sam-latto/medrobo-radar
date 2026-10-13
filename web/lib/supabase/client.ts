import { createBrowserClient } from "@supabase/ssr";

/**
 * Supabase in the browser. Used for sign-in and sign-out only; all data comes from the API
 * (docs/ARCHITECTURE.md section 2). The session lives in cookies so proxy.ts can see it.
 */
export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
  );
}
