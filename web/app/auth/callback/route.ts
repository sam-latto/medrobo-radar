import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";

/**
 * Google → Supabase → here. Swaps the one-time code for a session cookie.
 * A non-umich.edu account is rejected by the database's sign-up trigger, which Supabase reports
 * back as an error on this URL.
 */
export async function GET(request: NextRequest) {
  const { searchParams, origin } = request.nextUrl;
  const code = searchParams.get("code");

  if (searchParams.get("error")) {
    const description = searchParams.get("error_description") ?? "";
    // The trigger's rejection surfaces as "Database error saving new user"
    const reason = /database error/i.test(description) ? "not_allowed" : "failed";
    return NextResponse.redirect(`${origin}/sign-in?error=${reason}`);
  }
  if (!code) {
    return NextResponse.redirect(`${origin}/sign-in?error=failed`);
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.exchangeCodeForSession(code);
  if (error) {
    return NextResponse.redirect(`${origin}/sign-in?error=failed`);
  }
  return NextResponse.redirect(`${origin}/`);
}
