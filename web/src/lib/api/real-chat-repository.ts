// ============================================================
// Real Chat Repository — BFF-backed durable SSE client (run-first).
//
// The run-first contract splits streaming into two calls:
//   - createRun POSTs a new run (idempotency key dedupes on the server), learns the
//     durable runId from the `run.started` frame, and streams events live.
//   - subscribeRunEvents re-POSTs the SAME session + idempotency key with a
//     Last-Event-ID cursor, so the server resumes the existing run's live generation
//     without creating a second user message.
// Both track the SSE Last-Event-ID cursor so the caller can persist it between calls.
// ============================================================

import type {
  ChatEvent,
  CreatedRun,
  EmployeeActionApproval,
  Message,
  MessageStatus,
  RunSnapshot,
  RunStatus,
  RunStream,
  RunStreamOptions,
  Session,
} from "@/lib/domain/types";
import type { ChatRepository } from "@/lib/domain/repositories";

const CHAT_API = "/api/v1/internal/v1";

/** Typed chat-stream error carrying the BFF error envelope code. */
export class ChatStreamError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "ChatStreamError";
    this.code = code;
  }

  /** True when the end-user's session is invalid and re-login is required. */
  get isAuthRequired(): boolean {
    return this.code === "AUTH_REQUIRED";
  }
}

/**
 * Classify a non-OK BFF response. A 401 carrying the `AUTH_REQUIRED` envelope (or any
 * `AUTH_REQUIRED` envelope) becomes a typed `ChatStreamError` so callers can route to
 * re-login instead of treating it as a retriable stream drop.
 */
function toStreamError(
  status: number,
  errBody: { error?: { code?: string; message?: string } } | null,
): Error {
  const code = errBody?.error?.code;
  const message = errBody?.error?.message ?? `Chat stream failed: ${status}`;
  if (status === 401 || code === "AUTH_REQUIRED") {
    return new ChatStreamError("AUTH_REQUIRED", message);
  }
  return new Error(message);
}

interface SseFrame {
  id?: string;
  event?: string;
  data?: string;
}

function parseSseFrame(frame: string): SseFrame | null {
  const result: SseFrame = {};
  for (const line of frame.split("\n")) {
    if (line.startsWith("id:")) result.id = line.slice(3).trim();
    else if (line.startsWith("event:")) result.event = line.slice(6).trim();
    else if (line.startsWith("data:")) result.data = line.slice(5).trim();
  }
  return result.event || result.data ? result : null;
}

function toChatEvent(frame: SseFrame): ChatEvent | null {
  if (!frame.data) return null;
  let payload: Record<string, unknown> & {
    messageId?: string;
    text?: string;
    error?: { code?: string; message?: string };
    approvalId?: string;
    sessionId?: string;
    type?: string;
    title?: string;
    body?: string;
    createdAt?: string;
    action?: string;
    employeeId?: string;
    name?: string;
    position?: string | null;
    positionToSet?: string | null;
  };
  try {
    payload = JSON.parse(frame.data);
  } catch {
    return null;
  }
  switch (frame.event) {
    case "run.started":
    case "message-start":
      return { type: "message-start", messageId: payload.messageId ?? "" };
    case "message.delta":
      return { type: "token", messageId: payload.messageId ?? "", text: payload.text ?? "" };
    case "document.draft":
      return {
        type: "draft",
        draft: {
          id: payload.approvalId ?? "",
          sessionId: payload.sessionId ?? "",
          messageId: payload.messageId ?? "",
          title: payload.title ?? "",
          content: payload.body ?? "",
          category: (payload.type as "memory" | "interest" | "task" | "skill") ?? "interest",
          status: "pending_confirmation",
          createdAt: payload.createdAt ?? new Date().toISOString(),
        },
      };
    case "employee.action":
      return {
        type: "employee-action",
        approval: {
          id: payload.approvalId ?? "",
          sessionId: payload.sessionId ?? "",
          messageId: payload.messageId ?? "",
          employeeId: payload.employeeId ?? "",
          action: (payload.action as EmployeeActionApproval["action"]) ?? "fire",
          name: payload.name ?? "",
          position: payload.position ?? null,
          positionToSet: payload.positionToSet ?? null,
          status: "pending",
          createdAt: payload.createdAt ?? new Date().toISOString(),
        },
      };
    case "run.completed":
      return { type: "done", messageId: payload.messageId ?? "" };
    case "run.failed":
      return {
        type: "error",
        code: payload.error?.code ?? "STREAM_INTERRUPTED",
        message: payload.error?.message ?? "生成失败，请重试。",
      };
    default:
      return null;
  }
}

function toMessageStatus(status: string): MessageStatus {
  switch (status) {
    case "streaming":
      return "streaming";
    case "failed":
      return "error";
    default:
      return "completed";
  }
}

// --- SSE streaming helpers ---

/**
 * Stream a run response frame-by-frame, advancing the Last-Event-ID cursor.
 *
 * A clean HTTP end without a terminal SSE event means the connection dropped
 * mid-stream — the generator throws so the caller can resume with subscribeRunEvents.
 */
function streamRun(
  path: string,
  body: string,
  lastEventId: string | undefined,
  signal: AbortSignal | undefined,
): RunStream {
  let cursor = lastEventId;
  async function* events(): AsyncGenerator<ChatEvent> {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (cursor !== undefined) headers["Last-Event-ID"] = cursor;

    const res = await fetch(path, { method: "POST", headers, body, signal });
    if (!res.ok) {
      const errBody = await res.json().catch(() => null);
      throw toStreamError(res.status, errBody);
    }
    if (!res.body) throw new Error("Chat stream has no body");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let terminal = false;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          if (!terminal) throw new Error("stream ended without terminal event");
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          const parsed = parseSseFrame(frame);
          if (!parsed) continue;
          if (parsed.id) cursor = parsed.id;
          const chatEvent = toChatEvent(parsed);
          if (!chatEvent) continue;
          if (chatEvent.type === "done" || chatEvent.type === "error" || chatEvent.type === "draft")
            terminal = true;
          yield chatEvent;
        }
      }
    } finally {
      reader.releaseLock();
    }
  }
  return { events: events(), lastEventId: () => cursor };
}

/**
 * POST a new run, read frames until the `run.started` frame carries the durable
 * runId, then stream every event from that point onward (including the mapped
 * `message-start`). Resolves once the runId is known so the caller can persist it.
 */
function openRunStream(path: string, body: string, signal: AbortSignal | undefined): Promise<CreatedRun> {
  return new Promise<CreatedRun>((resolvePromise, rejectPromise) => {
    let cursor: string | undefined;
    let runId: string | undefined;
    let terminal = false;
    const pending: ChatEvent[] = [];
    let notify: (() => void) | undefined;
    let streamEnded = false;
    let streamError: unknown;

    const push = (event: ChatEvent) => {
      pending.push(event);
      if (notify) {
        notify();
        notify = undefined;
      }
    };

    async function* iterate(): AsyncGenerator<ChatEvent> {
      while (true) {
        if (pending.length > 0) {
          yield pending.shift() as ChatEvent;
          continue;
        }
        if (streamError !== undefined) throw streamError;
        if (streamEnded) return;
        await new Promise<void>((resolve) => {
          notify = resolve;
        });
      }
    }

    async function pump(): Promise<void> {
      try {
        const headers: Record<string, string> = { "Content-Type": "application/json" };
        const res = await fetch(path, { method: "POST", headers, body, signal });
        if (!res.ok) {
          const errBody = await res.json().catch(() => null);
          throw toStreamError(res.status, errBody);
        }
        if (!res.body) throw new Error("Chat stream has no body");

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) {
              if (!terminal) throw new Error("stream ended without terminal event");
              break;
            }
            buffer += decoder.decode(value, { stream: true });
            const frames = buffer.split("\n\n");
            buffer = frames.pop() ?? "";
            for (const frame of frames) {
              const parsed = parseSseFrame(frame);
              if (!parsed) continue;
              if (parsed.id) cursor = parsed.id;
              // The run.started frame carries the durable runId; map it to message-start.
              if (parsed.event === "run.started" && runId === undefined && parsed.data) {
                let payload: { messageId?: string; runId?: string };
                try {
                  payload = JSON.parse(parsed.data);
                } catch {
                  payload = {};
                }
                if (payload.runId) {
                  runId = payload.runId;
                  resolvePromise({
                    runId,
                    events: iterate(),
                    lastEventId: () => cursor,
                  });
                  push({ type: "message-start", messageId: payload.messageId ?? "" });
                  continue;
                }
              }
              const chatEvent = toChatEvent(parsed);
              if (!chatEvent) continue;
              if (chatEvent.type === "done" || chatEvent.type === "error" || chatEvent.type === "draft")
            terminal = true;
              push(chatEvent);
            }
          }
        } finally {
          reader.releaseLock();
        }
      } catch (error) {
        if (runId !== undefined) {
          streamError = error;
        } else {
          rejectPromise(error);
        }
      } finally {
        streamEnded = true;
        if (notify) {
          notify();
          notify = undefined;
        }
      }
    }

    void pump();
  });
}

export function createRealChatRepository(): ChatRepository {
  return {
    async listSessions(): Promise<Session[]> {
      const res = await fetch(`${CHAT_API}/sessions`);
      if (!res.ok) throw new Error("Failed to list sessions");
      const rows = (await res.json()) as Array<{
        id: string;
        title: string;
        createdAt: string;
        lastMessageAt: string;
      }>;
      return rows.map((row) => ({
        id: row.id,
        title: row.title,
        createdAt: row.createdAt,
        lastMessageAt: row.lastMessageAt,
      }));
    },

    async getMessages(sessionId: string): Promise<Message[]> {
      const res = await fetch(`${CHAT_API}/sessions/${sessionId}/messages`);
      if (!res.ok) throw new Error("Failed to get messages");
      const rows = (await res.json()) as Array<{
        id: string;
        sessionId: string;
        role: "user" | "assistant";
        content: string;
        status: string;
        createdAt: string;
      }>;
      return rows.map((row) => ({
        id: row.id,
        sessionId: row.sessionId,
        role: row.role,
        content: row.content,
        status: toMessageStatus(row.status),
        createdAt: row.createdAt,
      }));
    },

    async createRun(
      sessionId: string,
      content: string,
      idempotencyKey: string,
      options?: { signal?: AbortSignal },
    ): Promise<CreatedRun> {
      const path = `${CHAT_API}/sessions/${sessionId}/runs`;
      const body = JSON.stringify({ content, idempotency_key: idempotencyKey });
      return openRunStream(path, body, options?.signal);
    },

    subscribeRunEvents(options: RunStreamOptions): RunStream {
      const path = `${CHAT_API}/sessions/${options.sessionId}/runs`;
      const body = JSON.stringify({ content: options.content, idempotency_key: options.idempotencyKey });
      return streamRun(path, body, options.lastEventId, options.signal);
    },

    async getRun(runId: string): Promise<RunSnapshot | null> {
      const res = await fetch(`${CHAT_API}/runs/${runId}/events`);
      if (!res.ok) {
        if (res.status === 404) return null;
        const errBody = await res.json().catch(() => null);
        throw toStreamError(res.status, errBody);
      }
      if (!res.body) throw new Error("Run replay has no body");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let content = "";
      let status: RunStatus = "streaming";
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";
          for (const frame of frames) {
            const parsed = parseSseFrame(frame);
            if (!parsed?.data) continue;
            let payload: { messageId?: string; text?: string; error?: { code?: string; message?: string } };
            try {
              payload = JSON.parse(parsed.data);
            } catch {
              continue;
            }
            if (parsed.event === "message.delta" && typeof payload.text === "string") {
              content += payload.text;
            } else if (parsed.event === "run.completed") {
              status = "completed";
            } else if (parsed.event === "run.failed") {
              status = "error";
            }
          }
        }
      } finally {
        reader.releaseLock();
      }
      return { runId, status, content };
    },

    async createSession(title?: string, employeeId?: string): Promise<Session> {
      const body: { title?: string; employee_id?: string } = {};
      if (title !== undefined) body.title = title;
      if (employeeId !== undefined) body.employee_id = employeeId;
      const res = await fetch(`${CHAT_API}/sessions`, {
        method: "POST",
        ...(Object.keys(body).length > 0
          ? {
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
            }
          : {}),
      });
      if (!res.ok) throw new Error("Failed to create session");
      const row = (await res.json()) as {
        id: string;
        title: string;
        createdAt: string;
        lastMessageAt: string;
      };
      return {
        id: row.id,
        title: row.title,
        createdAt: row.createdAt,
        lastMessageAt: row.lastMessageAt,
      };
    },

    async deleteSession(sessionId: string): Promise<void> {
      const res = await fetch(`${CHAT_API}/sessions/${sessionId}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete session");
    },

    async renameSession(sessionId: string, title: string): Promise<void> {
      const res = await fetch(`${CHAT_API}/sessions/${sessionId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
      if (!res.ok) throw new Error("Failed to rename session");
    },
  };
}
