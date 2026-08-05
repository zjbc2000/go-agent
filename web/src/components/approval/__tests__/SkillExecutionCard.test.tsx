import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SkillExecutionCard } from "@/components/approval/SkillExecutionCard";
import type { SkillExecution } from "@/lib/domain/types";

const mocks = vi.hoisted(() => ({
  decideExecutionApproval: vi.fn(),
}));

vi.mock("@/lib/providers/repository-context", () => ({
  useRepositories: () => ({
    planning: { decideExecutionApproval: mocks.decideExecutionApproval },
    chat: {},
    auth: {},
  }),
}));

function pendingApproval(): SkillExecution {
  return {
    kind: "approval",
    approval: {
      approvalId: "app-1",
      status: "pending",
      expiresAt: "2026-01-01T00:00:00Z",
      createdAt: "2026-01-01T00:00:00Z",
    },
  };
}

function renderCard(
  execution: SkillExecution,
  props: { stepCount?: number; onRetry?: () => void } = {},
) {
  return render(
    <SkillExecutionCard
      documentTitle="技能文档"
      stepCount={props.stepCount ?? 1}
      execution={execution}
      onRetry={props.onRetry}
    />,
  );
}

describe("SkillExecutionCard", () => {
  beforeEach(() => {
    mocks.decideExecutionApproval.mockReset();
  });

  it("renders a pending approval with 等待确认, the summary, and the expiry", () => {
    renderCard(pendingApproval(), { stepCount: 3 });

    expect(screen.getByText("技能文档")).toBeInTheDocument();
    expect(screen.getByText(/共 3 个步骤/)).toBeInTheDocument();
    expect(screen.getByText("等待确认")).toBeInTheDocument();
    expect(screen.getByText(/有效期至/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认执行" })).toBeInTheDocument();
  });

  it("maps APPROVAL_EXPIRED from a failed confirm to the expired text", async () => {
    mocks.decideExecutionApproval.mockResolvedValue({
      decision: "error",
      error: { code: "APPROVAL_EXPIRED", message: "Execution approval has expired." },
    });

    renderCard(pendingApproval());
    fireEvent.click(screen.getByRole("button", { name: "确认执行" }));

    expect(await screen.findByText("该执行确认已过期")).toBeInTheDocument();
    // No live-preview of the plan remains actionable once the confirm is terminal.
    expect(screen.queryByRole("button", { name: "确认执行" })).not.toBeInTheDocument();
  });

  it("maps a SANDBOX_TIMEOUT run error to the timeout text", () => {
    renderCard({
      kind: "run",
      run: { runId: "r1", status: "failed", error: { code: "SANDBOX_TIMEOUT", message: "timed out" } },
    });

    expect(screen.getByText("执行超时，未完成的步骤未被提交。")).toBeInTheDocument();
  });

  it("maps SANDBOX_POLICY_DENIED and MCP_UNAVAILABLE to their accessible text", () => {
    renderCard({
      kind: "run",
      run: { runId: "r1", status: "policy_denied", error: { code: "SANDBOX_POLICY_DENIED", message: "denied" } },
    });
    expect(screen.getByText("策略已拒绝执行")).toBeInTheDocument();

    renderCard({
      kind: "run",
      run: { runId: "r2", status: "failed", error: { code: "MCP_UNAVAILABLE", message: "mcp down" } },
    });
    expect(screen.getByText("MCP 服务不可用")).toBeInTheDocument();
  });

  it("shows a retry action only when the API marks the error retryable", async () => {
    mocks.decideExecutionApproval.mockResolvedValue({
      decision: "error",
      error: { code: "MCP_UNAVAILABLE", message: "unavailable", retryable: true },
    });
    renderCard(pendingApproval());
    fireEvent.click(screen.getByRole("button", { name: "确认执行" }));

    expect(await screen.findByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("does not show a retry action when the error is not retryable", async () => {
    mocks.decideExecutionApproval.mockResolvedValue({
      decision: "error",
      error: { code: "MCP_UNAVAILABLE", message: "unavailable", retryable: false },
    });
    renderCard(pendingApproval());
    fireEvent.click(screen.getByRole("button", { name: "确认执行" }));

    await screen.findByText("MCP 服务不可用");
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });
});
