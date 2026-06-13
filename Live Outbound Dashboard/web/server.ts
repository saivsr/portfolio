// lib/supabase/server.ts — server-side Supabase client for the Next.js dashboard.
import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { cookies } from "next/headers";

/**
 * Server-side Supabase client using @supabase/ssr.
 *
 * Uses the ANON key by default so all queries flow through RLS exactly as a
 * browser client would. If you need to bypass RLS (e.g. service-only views),
 * pass `{ admin: true }` and we'll use SUPABASE_SERVICE_ROLE_KEY instead.
 *
 * NEVER call createClient({ admin: true }) from a route that returns HTML to
 * an unauthenticated user — the service key bypasses all RLS.
 */
export async function createClient(
  opts: { admin?: boolean } = {},
): Promise<ReturnType<typeof createServerClient>> {
  const cookieStore = await cookies();

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!url) throw new Error("NEXT_PUBLIC_SUPABASE_URL is not set");
  const key = opts.admin ? serviceKey : anonKey;
  if (!key) {
    throw new Error(
      opts.admin
        ? "SUPABASE_SERVICE_ROLE_KEY is not set"
        : "NEXT_PUBLIC_SUPABASE_ANON_KEY is not set",
    );
  }

  return createServerClient(url, key, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet: { name: string; value: string; options: CookieOptions }[]) {
        try {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options),
          );
        } catch {
          // `set` was called from a Server Component. This can be ignored if
          // there is middleware refreshing user sessions.
        }
      },
    },
  });
}
