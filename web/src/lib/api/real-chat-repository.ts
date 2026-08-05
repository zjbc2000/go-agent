// ============================================================
// Real Chat Repository — BFF-backed durable SSE client.
//
// Streams run events through the Next BFF (/api/v1/...), tracks the SSE
// Last-Event-ID cursor, and reconnects with the SAME idempotency key on network
// interruption so the server resumes the existing run without a second user message.
// ============================================================

import type { ChatEvent, Message, MessageStatus, Session } from "@/lib/domain/types";
import type { ChatRepository } from "@/lib/domain/repositories";

const CHAT_API = "/api/v1/internal/v1";

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
  let payload: { messageId?: string; text?: string; error?: { code?: string; message?: string } };
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

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

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

    async *sendMessage(
      sessionId: string,
      content: string,
      requestId: string,
    ): AsyncIterable<ChatEvent> {
      const path = `${CHAT_API}/sessions/${sessionId}/runs`;
      const body = JSON.stringify({ content, idempotency_key: requestId });
      let lastEventId: string | undefined;
      let attempt = 0;
      const maxAttempts = 5;

      while (true) {
        try {
          const headers: Record<string, string> = { "Content-Type": "application/json" };
          if (lastEventId !== undefined) headers["Last-Event-ID"] = lastEventId;

          const res = await fetch(path, { method: "POST", headers, body });
          if (!res.ok) {
            const errBody = await res.json().catch(() => null);
            throw new Error(errBody?.error?.message ?? `Chat stream failed: ${res.status}`);
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
                // A clean HTTP end without a terminal SSE event means the connection
                // dropped mid-stream — treat it as an interruption and reconnect.
                if (!terminal) throw new Error("stream ended without terminal event");
                break;
              }
              buffer += decoder.decode(value, { stream: true });
              const frames = buffer.split("\n\n");
              buffer = frames.pop() ?? "";
              for (const frame of frames) {
                const parsed = parseSseFrame(frame);
                if (!parsed) continue;
                if (parsed.id) lastEventId = parsed.id;
                const chatEvent = toChatEvent(parsed);
                if (!chatEvent) continue;
                if (chatEvent.type === "done" || chatEvent.type === "error") terminal = true;
                yield chatEvent;
              }
            }
          } finally {
            reader.releaseLock();
          }
          return; // Reached a clean terminal state.
        } catch (error) {
          attempt += 1;
          if (attempt >= maxAttempts) throw error;
          await sleep(300 * attempt);
          // Reconnect: the same idempotency key makes the server resume the existing
          // run, and Last-Event-ID skips what we already processed.
        }
      }
    },

    async stopGeneration(): Promise<void> {
      // No server-side stop endpoint yet; the stream ends on its own terminal event.
    },

    async createSession(): Promise<Session> {
      throw new Error("createSession is not implemented yet; use a seeded session");
    },
  };
}
