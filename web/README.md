# Industry Radar — web

Next.js front end (App Router, TypeScript, Tailwind v4). Design rules: `../docs/DESIGN.md`; how it fits with the API: `../docs/ARCHITECTURE.md`.

## Run locally

1. `cp .env.example .env.local` and fill in the Supabase project URL and publishable key (both are safe in the browser).
2. Start the API from the repo root: `uvicorn api.main:app --reload --port 8000`.
3. `npm install`, then `npm run dev` and open http://localhost:3000.

## How it's put together

- **Sign-in:** Supabase Auth with Google only (`app/sign-in`, `app/auth/callback`). Only `@umich.edu` accounts get through; the database's sign-up trigger enforces it.
- **`proxy.ts`** refreshes the session cookie and sends signed-out visitors to `/sign-in`. It's a convenience; the API checks the token on every request.
- **Data:** always from the API through `lib/api.ts`, which adds `Authorization: Bearer <token>`. Supabase is never used to read tables (its Data API is off).
- **Theme:** tokens live in the `@theme` block of `app/globals.css`. Use the named classes (`bg-teal`, `text-muted`, `text-h1`), never raw hex.
- **Screens:** `app/industries` (choose industries), and the signed-in shell in `app/(app)` with Briefing, Events, Companies and Jobs.
