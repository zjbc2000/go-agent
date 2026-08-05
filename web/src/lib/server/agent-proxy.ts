// ============================================================
// Agent service proxy — forwards browser /api/v1/* calls to the
// Python agent service with the signed internal token and the
// user's JWT, and streams the SSE response back untouched.
// ============================================================

const DEFAULT_AGENT_SERVICE_URL = "http://localhost:8000";
const DEFAULT_INTERNAL_TOKEN = "dev-internal-token";

export function agentServiceBaseUrl(): string {
  return process.env.AGENT_SERVICE_URL ?? DEFAULT_AGENT_SERVICE_URL;
}

export function internalToken(): string {
  return process.env.AGENT_INTERNAL_TOKEN ?? DEFAULT_INTERNAL_TOKEN;
}

/**
 * Build the agent-service request for a catch-all BFF path.
 *
 * The browser only ever talks to relative /api/v1/* paths on the Next origin; this
 * helper rewrites them to the agent service, attaches the internal service token (never
 * visible to the browser), forwards the end-user JWT for RLS, and passes through
 * Last-Event-ID so SSE replay resumes from the right cursor.
 */
export async function buildAgentRequest(
  path: string[],
  request: Request,
  accessToken: string,
): Promise<Request> {
  const target = new URL(`/${path.join("/")}`, agentServiceBaseUrl());
  target.search = new URL(request.url).search;

  const headers = new Headers();
  headers.set("x-internal-token", internalToken());
  headers.set("authorization", `Bearer ${accessToken}`);
  const lastEventId = request.headers.get("last-event-id");
  if (lastEventId) headers.set("last-event-id", lastEventId);
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);

  const method = request.method;
  const body = method === "GET" || method === "HEAD" ? undefined : await request.text();
  return new Request(target.toString(), { method, headers, body });
}

/**
 * Wrap the agent-service response for the browser, preserving the SSE content type and
 * disabling caching so every reconnect sees fresh bytes.
 */
export function proxyResponse(response: Response): Response {
  const headers = new Headers();
  headers.set("content-type", response.headers.get("content-type") ?? "text/event-stream");
  headers.set("cache-control", response.headers.get("cache-control") ?? "no-cache");
  return new Response(response.body, { status: response.status, headers });
}
