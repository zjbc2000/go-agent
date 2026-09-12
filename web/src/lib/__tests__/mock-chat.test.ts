import { describe, it, expect } from "vitest";
import { createMockChatRepository } from "@/lib/mock/chat-repository";

describe("MockChatRepository", () => {
  const repo = createMockChatRepository();

  it("lists pre-seeded sessions", async () => {
    const sessions = await repo.listSessions();
    expect(sessions.length).toBeGreaterThanOrEqual(2);
    expect(sessions[0]).toHaveProperty("id");
    expect(sessions[0]).toHaveProperty("title");
  });

  it("gets messages for a session", async () => {
    const messages = await repo.getMessages("sess_1");
    expect(messages.length).toBeGreaterThan(0);
    expect(messages[0].role).toBe("user");
  });

  it("returns empty array for unknown session", async () => {
    const messages = await repo.getMessages("nonexistent");
    expect(messages).toEqual([]);
  });

  it("creates a new session", async () => {
    const session = await repo.createSession();
    expect(session.title).toBe("新对话");
    expect(session.id).toMatch(/^sess_/);
  });

  it("creates a session with an employee title", async () => {
    const session = await repo.createSession("[秘书]苏曼", "emp-1");
    expect(session.title).toBe("[秘书]苏曼");
    expect(session.id).toMatch(/^sess_/);
  });

  it("createRun returns a run with a streaming event iterable", async () => {
    const run = await repo.createRun("sess_1", "测试消息", "req_test");
    expect(run.runId).toMatch(/^run_/);
    const events: unknown[] = [];
    for await (const event of run.events) {
      events.push(event);
    }
    expect(events.length).toBeGreaterThan(0);
    expect(events[0]).toHaveProperty("type", "message-start");
    const lastEvent = events[events.length - 1];
    expect(lastEvent).toHaveProperty("type", "done");
  });
});
