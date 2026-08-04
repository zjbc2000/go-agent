// ============================================================
// Real Chat Repository — placeholder, replace when API is ready
// ============================================================

import type { ChatEvent, Message, Session } from "@/lib/domain/types";
import type { ChatRepository } from "@/lib/domain/repositories";

/**
 * Creates a real chat repository backed by the backend API.
 *
 * Currently a placeholder that delegates to mock — swap out the fetch
 * calls when the LangGraph / SSE backend is ready.
 */
export function createRealChatRepository(): ChatRepository {
  const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

  return {
    async listSessions(): Promise<Session[]> {
      const res = await fetch(`${API_BASE}/api/sessions`);
      if (!res.ok) throw new Error("Failed to list sessions");
      return res.json();
    },

    async getMessages(sessionId: string): Promise<Message[]> {
      const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/messages`);
      if (!res.ok) throw new Error("Failed to get messages");
      return res.json();
    },

    async *sendMessage(
      sessionId: string,
      content: string,
      requestId: string,
    ): AsyncIterable<ChatEvent> {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionId, content, requestId }),
      });

      if (!res.ok || !res.body) {
        throw new Error("Chat stream failed");
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (line.startsWith("data: ")) {
              const data = line.slice(6).trim();
              if (data === "[DONE]") return;
              try {
                yield JSON.parse(data) as ChatEvent;
              } catch {
                // Skip malformed events
              }
            }
          }
        }
      } finally {
        reader.releaseLock();
      }
    },

    async stopGeneration(messageId: string): Promise<void> {
      await fetch(`${API_BASE}/api/chat/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messageId }),
      });
    },

    async createSession(): Promise<Session> {
      const res = await fetch(`${API_BASE}/api/sessions`, { method: "POST" });
      if (!res.ok) throw new Error("Failed to create session");
      return res.json();
    },
  };
}
