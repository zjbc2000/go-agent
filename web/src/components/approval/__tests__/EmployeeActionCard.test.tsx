import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EmployeeActionCard } from "@/components/approval/EmployeeActionCard";
import { useChatStore } from "@/lib/stores/chat-store";
import { PlanningApiError } from "@/lib/api/real-planning-repository";
import type { EmployeeActionApproval } from "@/lib/domain/types";

const mocks = vi.hoisted(() => ({
  decideEmployeeApproval: vi.fn(),
  onResolved: vi.fn(),
}));

vi.mock("@/lib/providers/repository-context", () => ({
  useRepositories: () => ({
    company: { decideEmployeeApproval: mocks.decideEmployeeApproval },
  }),
}));

function approval(overrides: Partial<EmployeeActionApproval> = {}): EmployeeActionApproval {
  return {
    id: "app-1",
    sessionId: "sess-1",
    messageId: "msg-1",
    employeeId: "emp-1",
    action: "fire",
    name: "李雷",
    position: "运营专员",
    positionToSet: null,
    status: "pending",
    createdAt: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function Harness({ initial }: { initial: EmployeeActionApproval }) {
  const drafts = useChatStore((s) => s.employeeDrafts);
  const current = drafts.find((d) => d.id === initial.id);
  if (!current) return null;
  return <EmployeeActionCard approval={current} onResolved={mocks.onResolved} />;
}

function renderCard(a: EmployeeActionApproval) {
  useChatStore.setState({ employeeDrafts: [a] });
  return render(<Harness initial={a} />);
}

describe("EmployeeActionCard", () => {
  beforeEach(() => {
    mocks.decideEmployeeApproval.mockReset();
    mocks.onResolved.mockReset();
    useChatStore.setState({ employeeDrafts: [] });
  });

  it("confirms a fire with a stable idempotency key and updates the store", async () => {
    mocks.decideEmployeeApproval.mockResolvedValue({
      approvalId: "app-1",
      decision: "confirmed",
      employee: {
        id: "emp-1",
        name: "李雷",
        position: "运营专员",
        prompt: "负责社区运营",
        status: "inactive",
        createdAt: "2026-01-01T00:00:00Z",
        updatedAt: "2026-01-01T00:00:00Z",
      },
    });

    renderCard(approval());
    fireEvent.click(screen.getByRole("button", { name: "确认解雇" }));

    await waitFor(() => expect(mocks.decideEmployeeApproval).toHaveBeenCalledTimes(1));
    expect(mocks.decideEmployeeApproval).toHaveBeenCalledWith(
      "app-1",
      "approve",
      "app-1:approve:v1",
    );

    await waitFor(() => {
      expect(useChatStore.getState().employeeDrafts[0].status).toBe("confirmed");
    });
    expect(mocks.onResolved).toHaveBeenCalledTimes(1);
    expect(screen.getByText("已确认")).toBeInTheDocument();
  });

  it("rejects and drops the pending card", async () => {
    mocks.decideEmployeeApproval.mockResolvedValue({
      approvalId: "app-1",
      decision: "rejected",
      employee: null,
    });

    renderCard(approval());
    fireEvent.click(screen.getByRole("button", { name: "驳回" }));

    await waitFor(() => expect(mocks.decideEmployeeApproval).toHaveBeenCalledTimes(1));
    expect(mocks.decideEmployeeApproval).toHaveBeenCalledWith("app-1", "reject", "app-1:reject:v1");
    await waitFor(() => expect(useChatStore.getState().employeeDrafts).toEqual([]));
    expect(mocks.onResolved).toHaveBeenCalledTimes(1);
  });

  it("maps APPROVAL_EXPIRED to a non-retriable expired card", async () => {
    mocks.decideEmployeeApproval.mockRejectedValue(
      new PlanningApiError("APPROVAL_EXPIRED", "Approval has expired."),
    );

    renderCard(approval());
    fireEvent.click(screen.getByRole("button", { name: "确认解雇" }));

    await waitFor(() => {
      expect(useChatStore.getState().employeeDrafts[0].status).toBe("expired");
    });
    expect(screen.getByText("已过期")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认解雇" })).not.toBeInTheDocument();
  });

  it("maps APPROVAL_CONFLICT to drop the stale card and refresh", async () => {
    mocks.decideEmployeeApproval.mockRejectedValue(
      new PlanningApiError("APPROVAL_CONFLICT", "Already decided."),
    );

    renderCard(approval());
    fireEvent.click(screen.getByRole("button", { name: "确认解雇" }));

    await waitFor(() => expect(useChatStore.getState().employeeDrafts).toEqual([]));
    expect(mocks.onResolved).toHaveBeenCalledTimes(1);
  });

  it("surfaces an adjust_position card with the target position", async () => {
    renderCard(approval({ action: "adjust_position", positionToSet: "产品经理" }));
    expect(screen.getByText(/调整岗位「李雷」岗位为「产品经理」/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认调整岗位" })).toBeInTheDocument();
  });
});
