"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import type { PlanDraft } from "@/lib/domain/types";
import { Save, X } from "lucide-react";

const draftSchema = z.object({
  title: z.string().min(1, "标题不能为空"),
  content: z.string().min(1, "内容不能为空"),
  category: z.enum(["memory", "interest", "task", "skill"]),
});

type DraftFormData = z.infer<typeof draftSchema>;

interface InlinePlanEditorProps {
  draft: PlanDraft;
  onSave: (data: DraftFormData) => Promise<void>;
  onCancel: () => void;
}

export function InlinePlanEditor({ draft, onSave, onCancel }: InlinePlanEditorProps) {
  const {
    register,
    handleSubmit,
    setValue,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<DraftFormData>({
    resolver: zodResolver(draftSchema),
    defaultValues: {
      title: draft.title,
      content: draft.content,
      category: draft.category as DraftFormData["category"],
    },
  });

  const category = watch("category");

  return (
    <form onSubmit={handleSubmit(onSave)} className="space-y-3">
      <div>
        <Input
          {...register("title")}
          placeholder="标题"
          className="h-8 text-sm"
        />
        {errors.title && (
          <p className="text-xs text-destructive mt-1">{errors.title.message}</p>
        )}
      </div>

      <div>
        <Textarea
          {...register("content")}
          placeholder="内容"
          rows={3}
          className="text-sm resize-none"
        />
        {errors.content && (
          <p className="text-xs text-destructive mt-1">{errors.content.message}</p>
        )}
      </div>

      <div className="flex items-center gap-2">
        <Select
          value={category}
          onValueChange={(v) => setValue("category", v as DraftFormData["category"])}
        >
          <SelectTrigger className="h-8 w-28 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="memory">记忆</SelectItem>
            <SelectItem value="interest">兴趣</SelectItem>
            <SelectItem value="task">任务</SelectItem>
            <SelectItem value="skill">Skill</SelectItem>
          </SelectContent>
        </Select>

        <div className="flex-1" />

        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onCancel}
          disabled={isSubmitting}
        >
          <X className="h-3.5 w-3.5 mr-1" />
          取消
        </Button>
        <Button type="submit" variant="default" size="sm" disabled={isSubmitting}>
          <Save className="h-3.5 w-3.5 mr-1" />
          {isSubmitting ? "保存中..." : "保存"}
        </Button>
      </div>
    </form>
  );
}
