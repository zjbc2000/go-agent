"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useRepositories } from "@/lib/providers/repository-context";
import type { ExecutionDecisionError, SkillExecution } from "@/lib/domain/types";
import { AlertTriangle, Check, Clock, RefreshCw } from "lucide-react";

// Distinct accessible UI text per backend error code. NEVER surface raw MCP
// payloads or secrets here — only the mapped, human-readable line.
const EXECUTION_ERROR_TEXT: Record<string, string> = {
  SANDBOX_POLICY_DENIED: "策略已拒绝执行",
  SANDBOX_TIMEOUT: "执行超时，未完成的步骤未被提交。",
  MCP_UNAVAILABLE: "MCP 服务不可用",
  APPROVAL_EXPIRED: "该执行确认已过期",
};

function executionErrorText(code: string): string {
  return EXECUTION_ERROR_TEXT[code] ?? "执行失败，请稍后重试";
}

function formatExpiry(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("zh-CN");
}

interface SkillExecutionCardProps {
  documentTitle: string;
  /** Immutable plan summary: the number of compiled steps, when known. */
  stepCount?: number;
  execution: SkillExecution;
  /** Called when the user retries a run-level error (confirm errors retry in-card). */
  onRetry?: () => void;
}

export function SkillExecutionCard({
  documentTitle,
  stepCount,
  execution,
  onRetry,
}: SkillExecutionCardProps) {
  const { planning } = useRepositories();
  const [confirming, setConfirming] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [confirmError, setConfirmError] = useState<ExecutionDecisionError | null>(null);

  const runError: ExecutionDecisionError | null =
    execution.kind === "run" && execution.run.error
      ? { code: execution.run.error.code, message: execution.run.error.message }
      : null;
  const error: ExecutionDecisionError | null = confirmError ?? runError;

  const handleConfirm = async () => {
    if (execution.kind !== "approval") return;
    setConfirming(true);
    setConfirmError(null);
    try {
      const result = await planning.decideExecutionApproval(
        execution.approval.approvalId,
        "approve",
        `${execution.approval.approvalId}:approve:v1`,
      );
      if (result.error) {
        setConfirmError(result.error);
        return;
      }
      setConfirmed(true);
    } catch {
      setConfirmError({ code: "INTERNAL_ERROR", message: "操作失败，请重试", retryable: true });
    } finally {
      setConfirming(false);
    }
  };

  const handleRetry = () => {
    if (confirmError && execution.kind === "approval") {
      handleConfirm();
      return;
    }
    onRetry?.();
  };

  return (
    <Card className="mt-3 border-brand/30 bg-surface">
      <CardHeader className="pb-1 pt-3 px-3">
        <div className="flex items-center gap-2 min-w-0">
          <CardTitle className="text-sm truncate">{documentTitle}</CardTitle>
          <Badge variant="secondary" className="text-xs ml-auto shrink-0">
            Skill 执行
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="px-3 pb-3">
        {stepCount !== undefined && (
          <p className="text-xs text-muted-foreground mb-2">共 {stepCount} 个步骤</p>
        )}

        {error ? (
          <div className="space-y-2">
            <p
              role="alert"
              className="text-xs text-destructive flex items-center gap-1"
            >
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              {executionErrorText(error.code)}
            </p>
            {error.retryable === true && (
              <Button variant="outline" size="sm" onClick={handleRetry}>
                <RefreshCw className="h-3.5 w-3.5 mr-1" />
                重试
              </Button>
            )}
          </div>
        ) : confirmed ? (
          <p className="text-xs text-muted-foreground flex items-center gap-1">
            <Check className="h-3.5 w-3.5 text-brand" />
            已确认，执行已排队
          </p>
        ) : execution.kind === "approval" ? (
          <>
            <div className="flex items-center gap-2 mb-2">
              <Badge variant="outline" className="text-xs">
                等待确认
              </Badge>
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                <Clock className="h-3 w-3" />
                有效期至 {formatExpiry(execution.approval.expiresAt)}
              </span>
            </div>
            <Button
              variant="default"
              size="sm"
              onClick={handleConfirm}
              disabled={confirming}
            >
              <Check className="h-3.5 w-3.5 mr-1" />
              确认执行
            </Button>
          </>
        ) : (
          <p className="text-xs text-muted-foreground">
            执行状态：{execution.run.status}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
