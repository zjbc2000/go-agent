"use client";

import { useState } from "react";
import type { PlanDraft } from "@/lib/domain/types";
import { useChatStore } from "@/lib/stores/chat-store";
import { useRepositories } from "@/lib/providers/repository-context";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { InlinePlanEditor } from "./InlinePlanEditor";
import { ApprovalActions } from "./ApprovalActions";
import { toast } from "sonner";
import { FileText } from "lucide-react";

const CATEGORY_LABELS: Record<string, string> = {
  memory: "记忆",
  interest: "兴趣",
  task: "任务",
  skill: "Skill",
};

interface PlanDraftCardProps {
  draft: PlanDraft;
}

export function PlanDraftCard({ draft }: PlanDraftCardProps) {
  const [isEditing, setIsEditing] = useState(false);
  const { updateDraft, removeDraft } = useChatStore();
  const { planning: planningRepo } = useRepositories();

  const handleConfirm = async () => {
    try {
      updateDraft(draft.id, { status: "saving" });
      await planningRepo.decideApproval(draft.id, "confirm");
      updateDraft(draft.id, { status: "confirmed" });
      toast.success("规划已确认，可在规划页查看");
    } catch {
      updateDraft(draft.id, { status: "pending_confirmation" });
      toast.error("确认失败，请重试");
    }
  };

  const handleReject = async () => {
    try {
      await planningRepo.decideApproval(draft.id, "reject");
      updateDraft(draft.id, { status: "rejected" });
      removeDraft(draft.id);
      toast("规划已驳回");
    } catch {
      toast.error("操作失败，请重试");
    }
  };

  const handleRegenerate = async () => {
    updateDraft(draft.id, { status: "regenerating" });
    toast("正在重新生成...");
  };

  const handleSave = async (data: { title: string; content: string; category: string }) => {
    try {
      updateDraft(draft.id, {
        title: data.title,
        content: data.content,
        category: data.category as PlanDraft["category"],
        status: "saving",
      });
      await planningRepo.decideApproval(draft.id, "confirm");
      updateDraft(draft.id, { status: "confirmed" });
      setIsEditing(false);
      toast.success("规划已保存并确认");
    } catch {
      updateDraft(draft.id, { status: "editing" });
      toast.error("保存失败，请重试");
    }
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
          <InlinePlanEditor
            draft={draft}
            onSave={handleSave}
            onCancel={() => {
              updateDraft(draft.id, { status: "pending_confirmation" });
              setIsEditing(false);
            }}
          />
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
