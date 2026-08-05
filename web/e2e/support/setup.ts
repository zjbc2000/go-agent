import { type APIRequestContext } from "@playwright/test";

// Local-dev only fallback; E2E runs against `supabase db reset`'d local stack.
const LOCAL_SUPABASE_URL = "http://localhost:54321";
const LOCAL_SERVICE_ROLE_KEY =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0.EGIM96RAZx35lJzdJsyH-qQwv8Hdp7fsn3W0YpN81IU";

export function serviceRoleEnv() {
  return {
    url: process.env.E2E_SUPABASE_URL ?? LOCAL_SUPABASE_URL,
    key: process.env.E2E_SUPABASE_SERVICE_ROLE_KEY ?? LOCAL_SERVICE_ROLE_KEY,
  };
}

/**
 * Ensure the seeded session row exists (agent-service pytest runs truncate the chat
 * tables, which deletes it). Idempotent upsert via the service role.
 */
export async function ensureSession(
  api: APIRequestContext,
  sessionId: string,
  userId: string,
): Promise<void> {
  const { url, key } = serviceRoleEnv();
  const headers = {
    apikey: key,
    Authorization: `Bearer ${key}`,
    "Content-Type": "application/json",
    Prefer: "resolution=ignore-duplicates",
  };
  await api.post(`${url}/rest/v1/sessions?on_conflict=id`, {
    headers,
    data: [{ id: sessionId, user_id: userId, title: "E2E Session" }],
  });
}

/**
 * Remove all messages, stream events, and runs for a session so each E2E run starts
 * from a clean slate (the user/session rows are re-seeded via seed.sql).
 */
export async function cleanSession(api: APIRequestContext, sessionId: string): Promise<void> {
  const { url, key } = serviceRoleEnv();
  const base = `${url}/rest/v1`;
  const headers = { apikey: key, Authorization: `Bearer ${key}` };

  const runs = (await (
    await api.get(`${base}/agent_runs?select=id&session_id=eq.${sessionId}`, { headers })
  ).json()) as Array<{ id: string }>;
  const runIds = runs.map((r) => r.id);
  if (runIds.length > 0) {
    const orFilter = `or=(${runIds.map((id) => `run_id.eq.${id}`).join(",")})`;
    await api.delete(`${base}/stream_events?${orFilter}`, { headers });
  }
  await api.delete(`${base}/messages?session_id=eq.${sessionId}`, { headers });
  await api.delete(`${base}/agent_runs?session_id=eq.${sessionId}`, { headers });
}
