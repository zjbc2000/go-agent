// ============================================================
// Supabase server client (route handlers / server components)
// ============================================================

import { createServerClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";
import { cookies } from "next/headers";
import type { User } from "@/lib/domain/types";

export async function createSupabaseServerClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) {
    throw new Error(
      "Supabase is not configured: set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY",
    );
  }
  const cookieStore = await cookies();
  return createServerClient(url, anonKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          cookiesToSet.forEach(({ name, value, options }) => cookieStore.set(name, value, options));
        } catch {
          // Called from a Server Component context where cookies cannot be set;
          // token refresh there is handled by the middleware.
        }
      },
    },
  });
}

export async function loadUser(
  supabase: SupabaseClient,
  authUser: { id: string; email?: string },
): Promise<User> {
  const { data: profile } = await supabase
    .from("profiles")
    .select("role, display_name")
    .eq("id", authUser.id)
    .maybeSingle();

  return {
    id: authUser.id,
    name: profile?.display_name ?? authUser.email ?? authUser.id,
    email: authUser.email ?? "",
    role: profile?.role === "admin" ? "admin" : "user",
  };
}
