import { beforeEach, describe, expect, it } from "vitest";
import { useChatStore } from "@/lib/stores/chat-store";

describe("chat store run state", () => {
  beforeEach(() => {
    useChatStore.setState({ runStates: {} });
  });

  it("creates a run with its resume context for a session", () => {
    const { startRun, getRunState } = useChatStore.getState();

    startRun("sess-1", { runId: "run-1", content: "hello", idempotencyKey: "req-1" });

    expect(getRunState("sess-1")).toEqual({
      runId: "run-1",
      content: "hello",
      idempotencyKey: "req-1",
      lastEventId: undefined,
    });
  });

  it("persists the last-event-id cursor without losing the resume context", () => {
    const { startRun, updateRunCursor, getRunState } = useChatStore.getState();

    startRun("sess-1", { runId: "run-1", content: "hello", idempotencyKey: "req-1" });
    updateRunCursor("sess-1", "3");
    updateRunCursor("sess-1", "7");

    const state = getRunState("sess-1");
    expect(state?.lastEventId).toBe("7");
    expect(state).toMatchObject({ runId: "run-1", content: "hello", idempotencyKey: "req-1" });
  });

  it("holds every field a resume needs: session key, run id, content, and cursor", () => {
    const { startRun, updateRunCursor, getRunState } = useChatStore.getState();

    startRun("sess-1", { runId: "run-1", content: "hello", idempotencyKey: "req-1" });
    updateRunCursor("sess-1", "2");

    expect(getRunState("sess-1")).toEqual({
      runId: "run-1",
      content: "hello",
      idempotencyKey: "req-1",
      lastEventId: "2",
    });
  });

  it("keeps run state isolated per session", () => {
    const { startRun, getRunState } = useChatStore.getState();

    startRun("sess-1", { runId: "run-1", content: "one", idempotencyKey: "req-1" });
    startRun("sess-2", { runId: "run-2", content: "two", idempotencyKey: "req-2" });

    expect(getRunState("sess-1")).toMatchObject({ runId: "run-1", idempotencyKey: "req-1" });
    expect(getRunState("sess-2")).toMatchObject({ runId: "run-2", idempotencyKey: "req-2" });
    expect(getRunState("sess-3")).toBeUndefined();
  });

  it("clears run state on a terminal outcome (completed or error)", () => {
    const { startRun, updateRunCursor, clearRunState, getRunState } = useChatStore.getState();

    startRun("sess-1", { runId: "run-1", content: "hello", idempotencyKey: "req-1" });
    updateRunCursor("sess-1", "3");
    clearRunState("sess-1");

    expect(getRunState("sess-1")).toBeUndefined();
  });
});
