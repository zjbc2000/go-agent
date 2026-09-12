"use client";

import { useState } from "react";
import type { EmployeeActionApproval, EmployeeApprovalStatus, EmployeeDecisionResult } from "@/lib/domain/types";
import { useChatStore } from "@/lib/stores/chat-store";
import { useRepositories } from "@/lib/providers/repository-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { UserRound } from "lucide-react";

const ACTION_LABELS: Record<string, string> = {
  fire: "解雇",
  rehire: "重新招聘",
  adjust_position: "调整岗位",
};

/** Stable per-action idempotency key so a retry of the same action reuses the key. */
function idempotencyKeyFor(approvalId: string, decision: string): string {
  return `${approvalId}:${decision}:v1`;
}

/** Read the envelope code from a real PlanningApiError or a mock Error message. */
function errorCode(error: unknown): string | null {
  if (error && typeof error === "object" && "code" in error) {
    return (error as { code: string }).code;
  }
  if (error instanceof Error) return error.message;
  return null;
}

interface EmployeeActionCardProps {
  approval: EmployeeActionApproval;
  /** Extra classes for the outer Card (defaults to the chat-stream margins). */
  className?: string;
  /** Called after a decision resolves so the parent (chat/company page) can refresh. */
  onResolved?: (result?: EmployeeDecisionResult) => void;
}

/**
 * A HITL card over a fire/rehire/adjust_position approval. Shared by the chat stream
 * (store-driven) and the company page (inline after a card-level fire/rehire click).
 * Approve applies the transition; reject resolves with no change.
 */
export function EmployeeActionCard({ approval, className, onResolved }: EmployeeActionCardProps) {
  const base = className ?? "mx-4 my-2";
  const [status, setStatus] = useState<EmployeeApprovalStatus>(approval.status);
  const [deciding, setDeciding] = useState(false);
  const { company: companyRepo } = useRepositories();
  const store = useChatStore.getState;

  const syncStore = (update: (drafts: EmployeeActionApproval[]) => EmployeeActionApproval[]) => {
    if (store().employeeDrafts.some((d) => d.id === approval.id)) {
      useChatStore.setState({ employeeDrafts: update(store().employeeDrafts) });
    }
  };

  const applyError = (error: unknown) => {
    const code = errorCode(error);
    if (code === "APPROVAL_EXPIRED") {
      setStatus("expired");
      syncStore((drafts) => drafts.map((d) => (d.id === approval.id ? { ...d, status: "expired" } : d)));
      toast.error("该审批已过期，请重新操作");
      return;
    }
    if (code === "APPROVAL_CONFLICT") {
      // Already decided elsewhere: drop the stale card and let the caller refresh.
      syncStore((drafts) => drafts.filter((d) => d.id !== approval.id));
      onResolved?.();
      toast("该审批已在别处处理，已刷新");
      return;
    }
    setStatus("pending");
    toast.error("操作失败，请重试");
  };

  const handleConfirm = async () => {
    if (deciding) return;
    setDeciding(true);
    try {
      const result = await companyRepo.decideEmployeeApproval(
        approval.id,
        "approve",
        idempotencyKeyFor(approval.id, "approve"),
      );
      setStatus("confirmed");
      syncStore((drafts) =>
        drafts.map((d) => (d.id === approval.id ? { ...d, status: "confirmed" } : d)),
      );
      toast.success(`${ACTION_LABELS[approval.action]}已确认`);
      onResolved?.(result);
    } catch (error) {
      applyError(error);
    } finally {
      setDeciding(false);
    }
  };

  const handleReject = async () => {
    if (deciding) return;
    setDeciding(true);
    try {
      const result = await companyRepo.decideEmployeeApproval(
        approval.id,
        "reject",
        idempotencyKeyFor(approval.id, "reject"),
      );
      setStatus("rejected");
      syncStore((drafts) => drafts.filter((d) => d.id !== approval.id));
      toast(`${ACTION_LABELS[approval.action]}已驳回`);
      onResolved?.(result);
    } catch (error) {
      applyError(error);
    } finally {
      setDeciding(false);
    }
  };

  const label = ACTION_LABELS[approval.action] ?? approval.action;
  const target = approval.action === "adjust_position" ? approval.positionToSet : null;

  const summary = target
    ? `${label}「${approval.name}」岗位为「${target}」`
    : `${label}「${approval.name}」`;

  if (status === "confirmed") {
    return (
      <Card className={`${base} border-brand/30 bg-surface`}>
        <CardHeader className="pb-1 pt-3 px-3">
          <div className="flex items-center gap-2">
            <UserRound className="h-4 w-4 text-brand" />
            <CardTitle className="text-sm">{summary}</CardTitle>
            <Badge variant="secondary" className="text-xs ml-auto">已确认</Badge>
          </div>
        </CardHeader>
        <CardContent className="px-3 pb-3">
          <p className="text-xs text-muted-foreground">{approval.name}（{approval.position}）</p>
        </CardContent>
      </Card>
    );
  }

  if (status === "expired") {
    return (
      <Card className={`${base} border-border bg-surface opacity-70`}>
        <CardHeader className="pb-1 pt-3 px-3">
          <div className="flex items-center gap-2">
            <UserRound className="h-4 w-4 text-muted-foreground" />
            <CardTitle className="text-sm line-through">{summary}</CardTitle>
            <Badge variant="outline" className="text-xs ml-auto">已过期</Badge>
          </div>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card className={`${base} border-brand/30 bg-surface`}>
      <CardHeader className="pb-1 pt-3 px-3">
        <div className="flex items-center gap-2">
          <UserRound className="h-4 w-4 text-brand" />
          <CardTitle className="text-sm">{summary}</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="px-3 pb-3">
        <p className="text-xs text-muted-foreground mb-1">
          {approval.name}（{approval.position ?? "未设置"}）
        </p>
        <div className="flex items-center gap-2">
          <Button
            variant="default"
            size="sm"
            onClick={handleConfirm}
            disabled={deciding || status !== "pending"}
          >
            确认{label}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleReject}
            disabled={deciding || status !== "pending"}
          >
            驳回
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
