import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PlanDraftCard } from "@/components/approval/PlanDraftCard";
import { useChatStore } from "@/lib/stores/chat-store";
import { PlanningApiError } from "@/lib/api/real-planning-repository";
import type { PlanDraft } from "@/lib/domain/types";

const mocks = vi.hoisted(() => ({
  decideApproval: vi.fn(),
  refresh: vi.fn(),
}));

vi.mock("@/lib/providers/repository-context", () => ({
  useRepositories: () => ({
    planning: { decideApproval: mocks.decideApproval },
    chat: {},
    auth: {},
  }),
}));

function draft(overrides: Partial<PlanDraft> = {}): PlanDraft {
  return {
    id: "app-1",
    sessionId: "sess-1",
    messageId: "msg-1",
    title: "original",
    content: "orig body",
    category: "task",
    status: "pending_confirmation",
    createdAt: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

/**
 * Mirrors the chat page, which reads drafts from the store and passes them to the
 * card. Subscribing to the store lets the card re-render as updateDraft/removeDraft
 * mutate draft state.
 */
function Harness({ initial, onRefresh }: { initial: PlanDraft; onRefresh?: () => void }) {
  const drafts = useChatStore((s) => s.drafts);
  const current = drafts.find((d) => d.id === initial.id);
  if (!current) return null;
  return <PlanDraftCard draft={current} onRefresh={onRefresh} />;
}

function renderDraft(d: PlanDraft, props: { onRefresh?: () => void } = {}) {
  useChatStore.setState({ drafts: [d] });
  return render(<Harness initial={d} {...props} />);
}

describe("PlanDraftCard server-result flow", () => {
  beforeEach(() => {
    mocks.decideApproval.mockReset();
    mocks.refresh.mockReset();
    useChatStore.setState({ drafts: [] });
  });

  it("sends the edited payload once with a stable idempotency key and updates from result.document", async () => {
    mocks.decideApproval.mockResolvedValue({
      approvalId: "app-1",
      decision: "confirmed",
      version: 2,
      originalPayload: { title: "original", content: "orig body", category: "task" },
      document: { id: "doc-1", title: "edited title", content: "edited body", category: "task", version: 2 },
    });

    renderDraft(draft());
    fireEvent.click(screen.getByRole("button", { name: "编辑后再确认" }));

    fireEvent.change(screen.getByPlaceholderText("标题"), { target: { value: "edited title" } });
    fireEvent.change(screen.getByPlaceholderText("内容"), { target: { value: "edited body" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(mocks.decideApproval).toHaveBeenCalledTimes(1));
    expect(mocks.decideApproval).toHaveBeenCalledWith({
      approvalId: "app-1",
      decision: "confirm",
      editedPayload: { title: "edited title", content: "edited body" },
      idempotencyKey: "app-1:confirm:v1",
    });

    await waitFor(() => {
      expect(useChatStore.getState().drafts[0].status).toBe("confirmed");
    });
    const confirmed = useChatStore.getState().drafts[0];
    expect(confirmed.title).toBe("edited title");
    expect(confirmed.content).toBe("edited body");
    expect(screen.getByText("edited title")).toBeInTheDocument();
  });

  it("maps APPROVAL_EXPIRED to a non-retriable expired card", async () => {
    mocks.decideApproval.mockRejectedValue(
      new PlanningApiError("APPROVAL_EXPIRED", "Approval has expired."),
    );

    renderDraft(draft());
    fireEvent.click(screen.getByRole("button", { name: "确认" }));

    await waitFor(() => {
      expect(useChatStore.getState().drafts[0].status).toBe("expired");
    });
    expect(screen.getByText("已过期")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "驳回" })).not.toBeInTheDocument();
  });

  it("maps APPROVAL_CONFLICT to a refresh action and clears the stale draft", async () => {
    mocks.decideApproval.mockRejectedValue(
      new PlanningApiError("APPROVAL_CONFLICT", "Approval was already decided."),
    );

    renderDraft(draft(), { onRefresh: mocks.refresh });
    fireEvent.click(screen.getByRole("button", { name: "确认" }));

    await waitFor(() => expect(mocks.refresh).toHaveBeenCalledTimes(1));
    expect(useChatStore.getState().drafts).toEqual([]);
  });

  it("maps VALIDATION_FAILED to an inline field error", async () => {
    mocks.decideApproval.mockRejectedValue(
      new PlanningApiError("VALIDATION_FAILED", "edited_payload.title is required."),
    );

    renderDraft(draft());
    fireEvent.click(screen.getByRole("button", { name: "编辑后再确认" }));
    fireEvent.change(screen.getByPlaceholderText("标题"), { target: { value: "edited" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(screen.getByText("edited_payload.title is required.")).toBeInTheDocument(),
    );
    expect(useChatStore.getState().drafts[0].status).toBe("editing");
  });

  it("preserves edited form values after a transport failure", async () => {
    mocks.decideApproval.mockRejectedValue(new Error("network down"));

    renderDraft(draft());
    fireEvent.click(screen.getByRole("button", { name: "编辑后再确认" }));
    fireEvent.change(screen.getByPlaceholderText("标题"), { target: { value: "draft title" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(useChatStore.getState().drafts[0].status).toBe("editing");
    });
    // The editor stays mounted with the typed value intact.
    expect(screen.getByPlaceholderText("标题")).toHaveValue("draft title");
    expect(mocks.decideApproval).toHaveBeenCalledTimes(1);
  });
});
