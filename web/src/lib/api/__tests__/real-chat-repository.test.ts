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

const DOCUMENT_DRAFT = (
  seq: number,
  messageId: string,
  approvalId: string,
  sessionId: string,
  type: string,
  title: string,
  body: string,
) =>
  `id: ${seq}\nevent: document.draft\ndata: ${JSON.stringify({
    approvalId,
    sessionId,
    messageId,
    type,
    title,
    body,
    createdAt: "2026-08-06T00:00:00.000Z",
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

  it("maps document.draft SSE to a pending-confirmation PlanDraft", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(
        RUN_STARTED(1, "msg-1", "run-1") +
          DELTA(2, "msg-1", "我对") +
          DOCUMENT_DRAFT(3, "msg-1", "app-1", "sess-1", "interest", "学习摄影", "目标：构图"),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const run = await createRealChatRepository().createRun("sess-1", "我想学摄影", "req-1");
    const events: unknown[] = [];
    for await (const event of run.events) events.push(event);

    expect(events).toEqual([
      { type: "message-start", messageId: "msg-1" },
      { type: "token", messageId: "msg-1", text: "我对" },
      {
        type: "draft",
        draft: {
          id: "app-1",
          sessionId: "sess-1",
          messageId: "msg-1",
          title: "学习摄影",
          content: "目标：构图",
          category: "interest",
          status: "pending_confirmation",
          createdAt: "2026-08-06T00:00:00.000Z",
        },
      },
    ]);
  });

  it("treats a draft-terminated stream as settled (no reconnect, no throw)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(RUN_STARTED(1, "msg-1", "run-1") + DOCUMENT_DRAFT(2, "msg-1", "app-1", "sess-1", "task", "计划", "内容")),
    );
    vi.stubGlobal("fetch", fetchMock);

    const run = await createRealChatRepository().createRun("sess-1", "帮我规划", "req-1");
    const events: unknown[] = [];
    for await (const event of run.events) events.push(event);

    // No "stream ended without terminal event" throw, and no reconnect (single POST).
    expect(events.some((e) => (e as ChatEvent).type === "draft")).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("subscribeRunEvents replay ending in document.draft terminates cleanly", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(DOCUMENT_DRAFT(3, "msg-1", "app-1", "sess-1", "interest", "标题", "正文")),
    );
    vi.stubGlobal("fetch", fetchMock);

    const stream = createRealChatRepository().subscribeRunEvents({
      sessionId: "sess-1",
      content: "x",
      idempotencyKey: "req-1",
      lastEventId: "2",
    });
    const events: unknown[] = [];
    for await (const event of stream.events) events.push(event);

    expect(events.some((e) => (e as ChatEvent).type === "draft")).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("createSession POSTs title + employee_id for an employee-bound session", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        id: "sess-1",
        title: "[秘书]苏曼",
        createdAt: "2026-01-01T00:00:00Z",
        lastMessageAt: "2026-01-01T00:00:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const session = await createRealChatRepository().createSession("[秘书]苏曼", "emp-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/internal/v1/sessions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ title: "[秘书]苏曼", employee_id: "emp-1" }),
      }),
    );
    expect(session.title).toBe("[秘书]苏曼");
  });

  it("createSession with no args sends an empty POST body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        id: "sess-2",
        title: "New chat",
        createdAt: "2026-01-01T00:00:00Z",
        lastMessageAt: "2026-01-01T00:00:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createRealChatRepository().createSession();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/internal/v1/sessions",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
