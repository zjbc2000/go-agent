import { afterEach, describe, expect, it, vi } from "vitest";
import { buildAgentRequest, proxyResponse } from "@/lib/server/agent-proxy";

describe("agent-proxy", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("forwards to the agent service with the signed internal token and user JWT", async () => {
    vi.stubEnv("AGENT_SERVICE_URL", "http://agent:8000");
    vi.stubEnv("AGENT_INTERNAL_TOKEN", "s3cret");

    const incoming = new Request("http://localhost:3000/api/v1/internal/v1/runs/abc/events", {
      headers: { "last-event-id": "3" },
    });
    const req = await buildAgentRequest(
      ["internal", "v1", "runs", "abc", "events"],
      incoming,
      "user-token",
    );

    expect(req.url).toBe("http://agent:8000/internal/v1/runs/abc/events");
    expect(req.headers.get("x-internal-token")).toBe("s3cret");
    expect(req.headers.get("authorization")).toBe("Bearer user-token");
    expect(req.headers.get("last-event-id")).toBe("3");
  });

  it("preserves the Last-Event-ID cursor when present", async () => {
    const incoming = new Request("http://localhost:3000/api/v1/internal/v1/sessions/s/runs", {
      method: "POST",
      headers: { "last-event-id": "5", "content-type": "application/json" },
      body: JSON.stringify({ content: "hi", idempotency_key: "k" }),
    });
    const req = await buildAgentRequest(
      ["internal", "v1", "sessions", "s", "runs"],
      incoming,
      "t",
    );
    expect(req.headers.get("last-event-id")).toBe("5");
  });

  it("forwards the POST body and content type", async () => {
    const incoming = new Request("http://localhost:3000/api/v1/internal/v1/sessions/s/runs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content: "hi", idempotency_key: "k" }),
    });
    const req = await buildAgentRequest(
      ["internal", "v1", "sessions", "s", "runs"],
      incoming,
      "t",
    );
    expect(req.method).toBe("POST");
    expect(req.headers.get("content-type")).toBe("application/json");
    expect(await req.text()).toBe(JSON.stringify({ content: "hi", idempotency_key: "k" }));
  });

  it("preserves text/event-stream and forces no-cache on the proxied response", async () => {
    const encoder = new TextEncoder();
    const upstream = new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode("id: 1\nevent: run.started\ndata: {}\n\n"));
          controller.close();
        },
      }),
      {
        status: 200,
        headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
      },
    );

    const proxied = proxyResponse(upstream);
    expect(proxied.headers.get("content-type")).toBe("text/event-stream");
    expect(proxied.headers.get("cache-control")).toBe("no-cache");
    expect(await proxied.text()).toContain("event: run.started");
  });
});
