import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatStreamError, createRealChatRepository } from "@/lib/api/real-chat-repository";
import type { ChatEvent } from "@/lib/domain/types";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// --- SSE frame builders (mirror app/chat/events.py format_sse) ---

function sseResponse(frames: string): Response {
  return new Response(frames, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

const RUN_STARTED = (seq: number, messageId: string, runId: string) =>
  `id: ${seq}\nevent: run.started\ndata: ${JSON.stringify({ messageId, runId })}\n\n`;

const DELTA = (seq: number, messageId: string, text: string) =>
  `id: ${seq}\nevent: message.delta\ndata: ${JSON.stringify({ messageId, text })}\n\n`;

const COMPLETED = (seq: number, messageId: string) =>
  `id: ${seq}\nevent: run.completed\ndata: ${JSON.stringify({ messageId })}\n\n`;

const RUN_FAILED = (seq: number, messageId: string, code: string, message: string) =>
  `id: ${seq}\nevent: run.failed\ndata: ${JSON.stringify({
    messageId,
    error: { code, message },
  })}\n\n`;

describe("RealChatRepository (run-first)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("createRun POSTs the run and streams events with the run id and cursor", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(
        RUN_STARTED(1, "msg-1", "run-1") +
          DELTA(2, "msg-1", "Hel") +
          DELTA(3, "msg-1", "lo") +
          COMPLETED(4, "msg-1"),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const run = await createRealChatRepository().createRun("sess-1", "hello", "req-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/internal/v1/sessions/sess-1/runs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ content: "hello", idempotency_key: "req-1" }),
      }),
    );
    expect(run.runId).toBe("run-1");

    const events: unknown[] = [];
    for await (const event of run.events) events.push(event);
    expect(events).toEqual([
      { type: "message-start", messageId: "msg-1" },
      { type: "token", messageId: "msg-1", text: "Hel" },
      { type: "token", messageId: "msg-1", text: "lo" },
      { type: "done", messageId: "msg-1" },
    ]);
    expect(run.lastEventId()).toBe("4");
  });

  it("reconnects with the same idempotency key without creating a second user message", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        // First POST: run.started + one delta, then the connection drops (no terminal event).
        sseResponse(RUN_STARTED(1, "msg-1", "run-1") + DELTA(2, "msg-1", "Hello fr")),
      )
      .mockResolvedValueOnce(
        // Reconnect POST: the server replays after Last-Event-ID and completes.
        sseResponse(DELTA(3, "msg-1", "om the Goudan agent!") + COMPLETED(4, "msg-1")),
      );
    vi.stubGlobal("fetch", fetchMock);

    const repo = createRealChatRepository();
    const run = await repo.createRun("sess-1", "hello", "req-1");

    const first: ChatEvent[] = [];
    try {
      for await (const event of run.events) first.push(event);
    } catch {
      // The dropped stream surfaces as an interruption for the caller to resume.
    }
    expect(first.map((e) => e.type)).toEqual(["message-start", "token"]);

    const resumed = repo.subscribeRunEvents({
      sessionId: "sess-1",
      content: "hello",
      idempotencyKey: "req-1",
      lastEventId: run.lastEventId(),
    });
    const rest: ChatEvent[] = [];
    for await (const event of resumed.events) rest.push(event);
    expect(rest).toEqual([
      { type: "token", messageId: "msg-1", text: "om the Goudan agent!" },
      { type: "done", messageId: "msg-1" },
    ]);

    // The reconnect re-POSTs the SAME idempotency key (so the server dedupes and
    // never creates a second user message) and resumes from the Last-Event-ID cursor.
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/internal/v1/sessions/sess-1/runs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ content: "hello", idempotency_key: "req-1" }),
        headers: expect.objectContaining({ "Last-Event-ID": "2" }),
      }),
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("subscribeRunEvents threads the AbortSignal to the resume request", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(sseResponse(DELTA(3, "msg-1", "rest") + COMPLETED(4, "msg-1")));
    vi.stubGlobal("fetch", fetchMock);

    const controller = new AbortController();
    const stream = createRealChatRepository().subscribeRunEvents({
      sessionId: "sess-1",
      content: "hello",
      idempotencyKey: "req-1",
      lastEventId: "2",
      signal: controller.signal,
    });

    const events: unknown[] = [];
    for await (const event of stream.events) events.push(event);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/internal/v1/sessions/sess-1/runs",
      expect.objectContaining({ signal: controller.signal }),
    );
  });

  it("maps run.failed to a retriable terminal error event", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(
        RUN_STARTED(1, "msg-1", "run-1") + RUN_FAILED(2, "msg-1", "STREAM_INTERRUPTED", "生成失败，请重试。"),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const run = await createRealChatRepository().createRun("sess-1", "hello", "req-1");
    const events: unknown[] = [];
    for await (const event of run.events) events.push(event);

    expect(events[events.length - 1]).toEqual({
      type: "error",
      code: "STREAM_INTERRUPTED",
      message: "生成失败，请重试。",
    });
  });

  it("createRun throws ChatStreamError on a 401 AUTH_REQUIRED envelope", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ error: { code: "AUTH_REQUIRED", message: "请先登录。" } }, 401));
    vi.stubGlobal("fetch", fetchMock);

    const promise = createRealChatRepository().createRun("sess-1", "hello", "req-1");
    await expect(promise).rejects.toBeInstanceOf(ChatStreamError);
    await expect(promise).rejects.toMatchObject({ code: "AUTH_REQUIRED" });
  });

  it("subscribeRunEvents throws ChatStreamError on a 401 AUTH_REQUIRED envelope", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ error: { code: "AUTH_REQUIRED", message: "请先登录。" } }, 401));
    vi.stubGlobal("fetch", fetchMock);

    const stream = createRealChatRepository().subscribeRunEvents({
      sessionId: "sess-1",
      content: "hello",
      idempotencyKey: "req-1",
      lastEventId: "2",
    });
    // Iteration itself must reject with the typed error.
    const promise = stream.events[Symbol.asyncIterator]().next();
    await expect(promise).rejects.toBeInstanceOf(ChatStreamError);
    await expect(promise).rejects.toMatchObject({ code: "AUTH_REQUIRED" });
  });

  it("getRun reconstructs a run snapshot from the event replay", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(
        RUN_STARTED(1, "msg-1", "run-1") +
          DELTA(2, "msg-1", "Hel") +
          DELTA(3, "msg-1", "lo") +
          COMPLETED(4, "msg-1"),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const snapshot = await createRealChatRepository().getRun("run-1");

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/internal/v1/runs/run-1/events");
    expect(snapshot).toEqual({ runId: "run-1", status: "completed", content: "Hello" });
  });
});
