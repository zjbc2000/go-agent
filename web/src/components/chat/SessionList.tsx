"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useChatStore } from "@/lib/stores/chat-store";
import { useRepositories } from "@/lib/providers/repository-context";
import { useEffect } from "react";
import { cn } from "@/lib/utils";
import { MessageSquare } from "lucide-react";

export function SessionList() {
  const pathname = usePathname();
  const { sessions, loadSessions, activeSessionId, setActiveSession } = useChatStore();
  const { chat: chatRepo } = useRepositories();

  useEffect(() => {
    loadSessions(chatRepo);
  }, [chatRepo, loadSessions]);

  if (sessions.length === 0) {
    return (
      <p className="text-xs text-muted-foreground py-4 text-center">
        暂无对话记录
      </p>
    );
  }

  return (
    <div className="space-y-0.5">
      <p className="text-xs font-medium text-muted-foreground px-1 py-2">最近对话</p>
      {sessions.map((s) => {
        const isActive = pathname.includes(s.id) ||
          (activeSessionId === s.id && pathname === "/chat");
        return (
          <Link key={s.id} href={`/chat/${s.id}`} onClick={() => setActiveSession(s.id)}>
            <div
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                isActive
                  ? "bg-secondary text-foreground font-medium"
                  : "text-muted-foreground hover:bg-surface-hover hover:text-foreground",
              )}
            >
              <MessageSquare className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{s.title}</span>
            </div>
          </Link>
        );
      })}
    </div>
  );
}
