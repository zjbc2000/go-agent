"use client";

import { Check, X, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { PlanDraftStatus } from "@/lib/domain/types";

interface ApprovalActionsProps {
  status: PlanDraftStatus;
  onConfirm: () => void;
  onReject: () => void;
  onRegenerate: () => void;
}

export function ApprovalActions({
  status,
  onConfirm,
  onReject,
  onRegenerate,
}: ApprovalActionsProps) {
  const isPending = status === "pending_confirmation";
  const isEditing = status === "editing" || status === "saving";
  const isRegenerating = status === "regenerating";

  return (
    <div className="flex items-center gap-2 pt-2">
      {(isPending || isEditing) && (
        <>
          <Button
            variant="default"
            size="sm"
            onClick={onConfirm}
            disabled={!isPending && !isEditing}
          >
            <Check className="h-3.5 w-3.5 mr-1" />
            确认
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={onReject}
            disabled={!isPending && !isEditing}
          >
            <X className="h-3.5 w-3.5 mr-1" />
            驳回
          </Button>
        </>
      )}
      <Button
        variant="ghost"
        size="sm"
        onClick={onRegenerate}
        disabled={isRegenerating}
      >
        <RefreshCw
          className={`h-3.5 w-3.5 mr-1 ${isRegenerating ? "animate-spin" : ""}`}
        />
        重新生成
      </Button>
    </div>
  );
}
