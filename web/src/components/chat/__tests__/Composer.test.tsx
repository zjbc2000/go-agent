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
  listDocuments: vi.fn(),
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
    planning: { listDocuments: mocks.listDocuments },
  }),
}));

const PLANNING_DOCS = [
  { id: "doc-1", title: "学习 Rust 编程", content: "每周学 Rust 5 小时", category: "interest" },
  { id: "doc-2", title: "周末晨跑计划", content: "周六日晨跑 5 公里", category: "task" },
];

describe("Composer AUTH_REQUIRED handling", () => {
  beforeEach(() => {
    mocks.createRun.mockReset();
    mocks.subscribeRunEvents.mockReset();
    mocks.logout.mockReset();
    mocks.replace.mockReset();
    mocks.listDocuments.mockReset();
    mocks.listDocuments.mockResolvedValue(PLANNING_DOCS);
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

  it("opens the planning picker and lists documents", async () => {
    render(<Composer />);
    fireEvent.click(screen.getByRole("button", { name: "引用规划文档" }));

    await waitFor(() => expect(mocks.listDocuments).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText("学习 Rust 编程")).toBeInTheDocument());
  });

  it("selecting a planning doc adds a removable chip", async () => {
    render(<Composer />);
    fireEvent.click(screen.getByRole("button", { name: "引用规划文档" }));
    await waitFor(() => expect(screen.getByText("学习 Rust 编程")).toBeInTheDocument());

    fireEvent.click(screen.getByText("学习 Rust 编程"));
    // Close the dialog (base-ui marks the inert background content aria-hidden while
    // the dialog is open, so the chip below the input is only queryable after close).
    fireEvent.click(screen.getByRole("button", { name: "完成" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "移除 学习 Rust 编程" })).toBeInTheDocument(),
    );
  });

  it("removing a chip clears the selection", async () => {
    render(<Composer />);
    fireEvent.click(screen.getByRole("button", { name: "引用规划文档" }));
    await waitFor(() => expect(screen.getByText("学习 Rust 编程")).toBeInTheDocument());
    fireEvent.click(screen.getByText("学习 Rust 编程"));
    fireEvent.click(screen.getByRole("button", { name: "完成" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "移除 学习 Rust 编程" })).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole("button", { name: "移除 学习 Rust 编程" }));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "移除 学习 Rust 编程" })).not.toBeInTheDocument(),
    );
  });

  it("send appends the selected planning doc content to the message", async () => {
    mocks.createRun.mockResolvedValue({
      runId: "run-1",
      events: (async function* () {
        yield { type: "done", messageId: "msg-1" };
      })(),
      lastEventId: () => "1",
    });

    render(<Composer />);
    fireEvent.click(screen.getByRole("button", { name: "引用规划文档" }));
    await waitFor(() => expect(screen.getByText("学习 Rust 编程")).toBeInTheDocument());
    fireEvent.click(screen.getByText("学习 Rust 编程"));
    fireEvent.click(screen.getByRole("button", { name: "完成" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "移除 学习 Rust 编程" })).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByPlaceholderText("输入你的需求..."), {
      target: { value: "根据这些规划回答我" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));

    await waitFor(() =>
      expect(mocks.createRun).toHaveBeenCalledWith(
        "sess-1",
        expect.stringContaining("【参考规划：学习 Rust 编程】"),
        expect.any(String),
        expect.any(Object),
      ),
    );
    // Chips clear after send.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /^移除 / })).not.toBeInTheDocument(),
    );
  });
});
