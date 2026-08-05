"use client";

import { useState } from "react";
import type {
  PlanningDocument as PlanningDocumentType,
  SkillExecution,
} from "@/lib/domain/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useRepositories } from "@/lib/providers/repository-context";
import { VersionHistory } from "./VersionHistory";
import { SkillExecutionCard } from "@/components/approval/SkillExecutionCard";
import { Pencil, Trash2, History, Play } from "lucide-react";
import { toast } from "sonner";

const CATEGORY_LABELS: Record<string, string> = {
  memory: "记忆",
  interest: "兴趣",
  task: "任务",
  skill: "Skill",
};

interface PlanningDocumentProps {
  document: PlanningDocumentType;
  onUpdated: () => void;
}

/** Number of compiled steps in a skill manifest body, when it is valid JSON. */
function skillStepCount(content: string): number | undefined {
  try {
    const manifest = JSON.parse(content) as { steps?: unknown[] };
    if (Array.isArray(manifest.steps)) return manifest.steps.length;
  } catch {
    // Not a JSON manifest; the summary is omitted.
  }
  return undefined;
}

export function PlanningDocument({ document: doc, onUpdated }: PlanningDocumentProps) {
  const [showVersions, setShowVersions] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [execution, setExecution] = useState<SkillExecution | null>(null);
  const { planning: planningRepo } = useRepositories();

  const isSkill = doc.category === "skill";

  // "执行 Skill" on a skill-type document (a justified addition beyond the plan's
  // file list): request an execution and show the returned approval/run card.
  // Card state is client-side from the API response — no live polling.
  const handleExecute = async () => {
    setExecuting(true);
    try {
      const result = await planningRepo.requestExecution(
        doc.id,
        {},
        `skill-exec:${doc.id}:${Date.now()}`,
      );
      setExecution(result);
    } catch {
      toast.error("Skill 执行失败，请重试");
    } finally {
      setExecuting(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await planningRepo.deleteDocument(doc.id);
      setDeleted(true);
      toast.success("已删除");
      onUpdated();
    } catch {
      toast.error("删除失败");
      setDeleting(false);
    }
  };

  if (deleted) return null;

  return (
    <Card className="bg-surface">
      <CardHeader className="pb-2 pt-4 px-4">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <CardTitle className="text-sm truncate">{doc.title}</CardTitle>
            <Badge variant="secondary" className="text-xs shrink-0">
              {CATEGORY_LABELS[doc.category] ?? doc.category}
            </Badge>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {isSkill && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                onClick={handleExecute}
                disabled={executing}
              >
                <Play className="h-3 w-3 mr-1" />
                执行 Skill
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              aria-label="编辑"
              onClick={() => toast("编辑功能开发中")}
            >
              <Pencil className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              aria-label="查看历史版本"
              onClick={() => setShowVersions(!showVersions)}
            >
              <History className="h-3.5 w-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-destructive hover:text-destructive"
              aria-label="删除"
              onClick={handleDelete}
              disabled={deleting}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="px-4 pb-4">
        <p className="text-xs text-muted-foreground whitespace-pre-wrap leading-relaxed">
          {doc.content}
        </p>
        <p className="text-[10px] text-muted-foreground mt-2">
          版本 {doc.version} · 更新于{" "}
          {new Date(doc.updatedAt).toLocaleDateString("zh-CN")}
        </p>

        {showVersions && (
          <div className="mt-3">
            <VersionHistory
              documentId={doc.id}
              onRestored={onUpdated}
            />
          </div>
        )}

        {execution && (
          <SkillExecutionCard
            documentTitle={doc.title}
            stepCount={skillStepCount(doc.content)}
            execution={execution}
          />
        )}
      </CardContent>
    </Card>
  );
}
