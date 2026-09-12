"use client";

import { useState } from "react";
import type { PlanDraft, PlanDraftStatus } from "@/lib/domain/types";
import { useChatStore } from "@/lib/stores/chat-store";
import { useRepositories } from "@/lib/providers/repository-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { InlinePlanEditor } from "./InlinePlanEditor";
import { ApprovalActions } from "./ApprovalActions";
import { PlanningApiError } from "@/lib/api/real-planning-repository";
import { toast } from "sonner";
import { FileText } from "lucide-react";

const CATEGORY_LABELS: Record<string, string> = {
  memory: "记忆",
  interest: "兴趣",
  task: "任务",
  skill: "Skill",
};

/** Stable per-action idempotency key so a retry of the same action reuses the key. */
function idempotencyKeyFor(approvalId: string, decision: string): string {
  return `${approvalId}:${decision}:v1`;
}

interface PlanDraftCardProps {
  draft: PlanDraft;
  /** Called when a decision collides with an already-resolved approval. */
  onRefresh?: () => void;
}

export function PlanDraftCard({ draft, onRefresh }: PlanDraftCardProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const { updateDraft, removeDraft } = useChatStore();
  const { planning: planningRepo, chat: chatRepo } = useRepositories();

  const handleConfirm = async () => {
    try {
      updateDraft(draft.id, { status: "saving" });
      const result = await planningRepo.decideApproval({
        approvalId: draft.id,
        decision: "confirm",
        idempotencyKey: idempotencyKeyFor(draft.id, "confirm"),
      });
      updateDraft(draft.id, {
        status: "confirmed",
        ...(result.document
          ? { title: result.document.title, content: result.document.content }
          : {}),
      });
      // The backend renames the session after an approval confirms ("兴趣·xxx"), so
      // refresh the session list to surface the new title in the sidebar.
      useChatStore.getState().loadSessions(chatRepo).catch(() => {});
      toast.success("规划已确认，可在规划页查看");
    } catch (error) {
      applyDecisionError(error, "pending_confirmation");
    }
  };

  const handleReject = async () => {
    try {
      await planningRepo.decideApproval({
        approvalId: draft.id,
        decision: "reject",
        idempotencyKey: idempotencyKeyFor(draft.id, "reject"),
      });
      updateDraft(draft.id, { status: "rejected" });
      removeDraft(draft.id);
      toast("规划已驳回");
    } catch (error) {
      applyDecisionError(error, "pending_confirmation");
    }
  };

  const handleSave = async (data: { title: string; content: string; category: string }) => {
    setFieldError(null);
    try {
      updateDraft(draft.id, {
        title: data.title,
        content: data.content,
        category: data.category as PlanDraft["category"],
        status: "saving",
      });
      const result = await planningRepo.decideApproval({
        approvalId: draft.id,
        decision: "confirm",
        editedPayload: { title: data.title, content: data.content },
        idempotencyKey: idempotencyKeyFor(draft.id, "confirm"),
      });
      updateDraft(draft.id, {
        status: "confirmed",
        title: result.document?.title ?? data.title,
        content: result.document?.content ?? data.content,
      });
      setIsEditing(false);
      toast.success("规划已保存并确认");
    } catch (error) {
      // Keep status "editing" so the editor stays mounted and edited values persist.
      applyDecisionError(error, "editing");
    }
  };

  const handleRegenerate = async () => {
    updateDraft(draft.id, { status: "regenerating" });
    toast("正在重新生成...");
  };

  const applyDecisionError = (error: unknown, resetStatus: PlanDraftStatus) => {
    if (error instanceof PlanningApiError) {
      if (error.code === "APPROVAL_EXPIRED") {
        updateDraft(draft.id, { status: "expired" });
        toast.error("该规划已过期，请重新生成");
        return;
      }
      if (error.code === "APPROVAL_CONFLICT") {
        // Already decided elsewhere: drop the stale draft and let the caller refresh.
        removeDraft(draft.id);
        onRefresh?.();
        toast("该规划已在别处处理，已刷新");
        return;
      }
      if (error.code === "VALIDATION_FAILED") {
        setFieldError(error.message || "提交内容校验失败");
        updateDraft(draft.id, { status: resetStatus });
        return;
      }
    }
    // Transport or unknown failure: revert so the card stays interactive; the editor
    // keeps its own form state, so edited values are preserved.
    updateDraft(draft.id, { status: resetStatus });
    toast.error(resetStatus === "editing" ? "保存失败，请重试" : "操作失败，请重试");
  };

  if (draft.status === "confirmed") {
    return (
      <Card className="mx-4 my-2 border-brand/30 bg-surface">
        <CardHeader className="pb-1 pt-3 px-3">
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-brand" />
            <CardTitle className="text-sm">{draft.title}</CardTitle>
            <Badge variant="secondary" className="text-xs ml-auto">已确认</Badge>
          </div>
        </CardHeader>
        <CardContent className="px-3 pb-3">
          <p className="text-xs text-muted-foreground whitespace-pre-wrap">
            {draft.content}
          </p>
        </CardContent>
      </Card>
    );
  }

  if (draft.status === "expired") {
    return (
      <Card className="mx-4 my-2 border-border bg-surface opacity-70">
        <CardHeader className="pb-1 pt-3 px-3">
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-muted-foreground" />
            <CardTitle className="text-sm line-through">{draft.title}</CardTitle>
            <Badge variant="outline" className="text-xs ml-auto">已过期</Badge>
          </div>
        </CardHeader>
        <CardContent className="px-3 pb-3">
          <p className="text-xs text-muted-foreground whitespace-pre-wrap">
            {draft.content}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="mx-4 my-2 border-brand/30 bg-surface">
      <CardHeader className="pb-1 pt-3 px-3">
        <div className="flex items-center gap-2">
          <FileText className="h-4 w-4 text-brand" />
          {isEditing ? (
            <span className="text-sm font-medium">编辑草稿</span>
          ) : (
            <CardTitle className="text-sm">{draft.title}</CardTitle>
          )}
          <Badge
            variant="outline"
            className="text-xs ml-auto"
          >
            {CATEGORY_LABELS[draft.category] ?? draft.category}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="px-3 pb-3">
        {isEditing ? (
          <>
            <InlinePlanEditor
              draft={draft}
              onSave={handleSave}
              onCancel={() => {
                updateDraft(draft.id, { status: "pending_confirmation" });
                setFieldError(null);
                setIsEditing(false);
              }}
            />
            {fieldError && (
              <p className="text-xs text-destructive mt-2">{fieldError}</p>
            )}
          </>
        ) : (
          <>
            <p className="text-xs text-muted-foreground whitespace-pre-wrap mb-2">
              {draft.content}
            </p>
            <ApprovalActions
              status={draft.status}
              onConfirm={handleConfirm}
              onReject={handleReject}
              onRegenerate={handleRegenerate}
            />
            <button
              type="button"
              className="text-xs text-muted-foreground hover:text-foreground mt-2"
              onClick={() => {
                updateDraft(draft.id, { status: "editing" });
                setFieldError(null);
                setIsEditing(true);
              }}
            >
              编辑后再确认
            </button>
          </>
        )}
      </CardContent>
    </Card>
  );
}
