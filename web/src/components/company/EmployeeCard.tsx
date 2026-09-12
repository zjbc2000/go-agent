"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Employee, EmployeeActionApproval } from "@/lib/domain/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useRepositories } from "@/lib/providers/repository-context";
import { useChatStore } from "@/lib/stores/chat-store";
import { EmployeeActionCard } from "@/components/approval/EmployeeActionCard";
import { Pencil, UserMinus, UserPlus, MessageSquare } from "lucide-react";
import { toast } from "sonner";

const STATUS_LABELS: Record<string, string> = { active: "在职", inactive: "已离职" };

interface EmployeeCardProps {
  employee: Employee;
  onUpdated: () => void;
}

export function EmployeeCard({ employee, onUpdated }: EmployeeCardProps) {
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(employee.name);
  const [editPosition, setEditPosition] = useState(employee.position);
  const [editPrompt, setEditPrompt] = useState(employee.prompt);
  const [saving, setSaving] = useState(false);
  const [pendingApproval, setPendingApproval] = useState<EmployeeActionApproval | null>(null);
  const { company: companyRepo, chat: chatRepo } = useRepositories();
  const { addSession } = useChatStore();
  const router = useRouter();

  const active = employee.status === "active";

  const handleChat = async () => {
    try {
      const session = await chatRepo.createSession(`[${employee.position}]${employee.name}`, employee.id);
      addSession(session);
      useChatStore.setState({ activeSessionId: session.id });
      router.push(`/chat/${session.id}`);
    } catch {
      toast.error("创建对话失败，请重试");
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await companyRepo.updateEmployee(employee.id, {
        name: editName.trim() || employee.name,
        position: editPosition.trim() || employee.position,
        prompt: editPrompt.trim() || employee.prompt,
      });
      setEditing(false);
      toast.success("已保存");
      onUpdated();
    } catch {
      toast.error("保存失败，请重试");
    } finally {
      setSaving(false);
    }
  };

  const handleFire = async () => {
    try {
      const approval = await companyRepo.requestEmployeeAction({
        employeeId: employee.id,
        action: "fire",
        idempotencyKey: `fire:${employee.id}:${Date.now()}`,
      });
      setPendingApproval(approval);
    } catch {
      toast.error("发起解雇审批失败，请重试");
    }
  };

  const handleRehire = async () => {
    try {
      const approval = await companyRepo.requestEmployeeAction({
        employeeId: employee.id,
        action: "rehire",
        idempotencyKey: `rehire:${employee.id}:${Date.now()}`,
      });
      setPendingApproval(approval);
    } catch {
      toast.error("发起重新招聘审批失败，请重试");
    }
  };

  return (
    <Card className="bg-surface">
      <CardHeader className="pb-2 pt-4 px-4">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <CardTitle className="text-sm truncate">{employee.name}</CardTitle>
            <Badge variant="secondary" className="text-xs shrink-0">{employee.position}</Badge>
            <Badge variant={active ? "default" : "outline"} className="text-xs shrink-0">
              {STATUS_LABELS[employee.status] ?? employee.status}
            </Badge>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {active ? (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                onClick={handleChat}
              >
                <MessageSquare className="h-3 w-3 mr-1" />
                对话
              </Button>
            ) : null}
            {active ? (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs text-destructive hover:text-destructive"
                onClick={handleFire}
              >
                <UserMinus className="h-3 w-3 mr-1" />
                解雇
              </Button>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                onClick={handleRehire}
              >
                <UserPlus className="h-3 w-3 mr-1" />
                重新招聘
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              aria-label="编辑"
              onClick={() => setEditing(true)}
            >
              <Pencil className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="px-4 pb-4">
        {editing ? (
          <div className="space-y-2">
            <Input
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              aria-label="姓名"
              className="h-8 text-sm"
            />
            <Input
              value={editPosition}
              onChange={(e) => setEditPosition(e.target.value)}
              aria-label="职位"
              className="h-8 text-sm"
            />
            <Textarea
              value={editPrompt}
              onChange={(e) => setEditPrompt(e.target.value)}
              aria-label="提示词"
              rows={3}
              className="text-sm resize-none"
            />
            <div className="flex gap-2 justify-end">
              <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
                取消
              </Button>
              <Button variant="default" size="sm" disabled={saving} onClick={handleSave}>
                {saving ? "保存中..." : "保存"}
              </Button>
            </div>
          </div>
        ) : (
          <p className="text-[10px] text-muted-foreground">
            更新于 {new Date(employee.updatedAt).toLocaleDateString("zh-CN")}
          </p>
        )}

        {pendingApproval && (
          <div className="mt-3">
            <EmployeeActionCard
              approval={pendingApproval}
              className="border-brand/30 bg-surface"
              onResolved={() => {
                setPendingApproval(null);
                onUpdated();
              }}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
