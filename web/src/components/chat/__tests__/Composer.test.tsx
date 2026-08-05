import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Composer } from "@/components/chat/Composer";
import { useChatStore } from "@/lib/stores/chat-store";
import { ChatStreamError } from "@/lib/api/real-chat-repository";

const mocks = vi.hoisted(() => ({
  createRun: vi.fn(),
  subscribeRunEvents: vi.fn(),
  logout: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace, push: vi.fn() }),
}));

vi.mock("@/lib/providers/repository-context", () => ({
  useRepositories: () => ({
    chat: {
      listSessions: vi.fn(),
      getMessages: vi.fn(),
      createRun: mocks.createRun,
      subscribeRunEvents: mocks.subscribeRunEvents,
      getRun: vi.fn(),
      createSession: vi.fn(),
    },
    auth: { logout: mocks.logout },
    planning: {},
  }),
}));

describe("Composer AUTH_REQUIRED handling", () => {
  beforeEach(() => {
    mocks.createRun.mockReset();
    mocks.subscribeRunEvents.mockReset();
    mocks.logout.mockReset();
    mocks.replace.mockReset();
    mocks.logout.mockResolvedValue(undefined);
    useChatStore.setState({
      activeSessionId: "sess-1",
      messages: [],
      chatState: "idle",
      showConnectionBanner: false,
      runStates: {},
    });
  });

  it("routes AUTH_REQUIRED to re-login without retrying the run as a drop", async () => {
    mocks.createRun.mockRejectedValue(new ChatStreamError("AUTH_REQUIRED", "请先登录。"));

    render(<Composer />);
    fireEvent.change(screen.getByPlaceholderText("输入你的需求..."), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));

    await waitFor(() => expect(mocks.logout).toHaveBeenCalled());
    expect(mocks.replace).toHaveBeenCalledWith("/login");
    expect(mocks.subscribeRunEvents).not.toHaveBeenCalled();
    expect(mocks.createRun).toHaveBeenCalledTimes(1);
  });

  it("still retries a genuine network drop as a reconnect, not re-login", async () => {
    mocks.createRun.mockResolvedValue({
      runId: "run-1",
      events: (async function* () {
        throw new Error("stream ended without terminal event");
      })(),
      lastEventId: () => "3",
    });
    mocks.subscribeRunEvents.mockReturnValue({
      events: (async function* () {
        yield { type: "done", messageId: "msg-1" };
      })(),
      lastEventId: () => "4",
    });

    render(<Composer />);
    fireEvent.change(screen.getByPlaceholderText("输入你的需求..."), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));

    await waitFor(() => expect(mocks.subscribeRunEvents).toHaveBeenCalled());
    await waitFor(() => expect(useChatStore.getState().chatState).toBe("completed"));
    expect(mocks.logout).not.toHaveBeenCalled();
    expect(mocks.replace).not.toHaveBeenCalled();
    expect(mocks.createRun).toHaveBeenCalledTimes(1);
  });
});
