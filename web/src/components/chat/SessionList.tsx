"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useChatStore } from "@/lib/stores/chat-store";
import { useRepositories } from "@/lib/providers/repository-context";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { MessageSquare, Trash2 } from "lucide-react";

/** Split a title like "[秘书]苏曼" into the badge position and the plain name. */
function splitEmployeeTitle(title: string): { position?: string; name: string } {
  const match = /^\[([^\]]+)\](.*)$/.exec(title);
  if (!match) return { name: title };
  return { position: match[1], name: match[2] || title };
}

export function SessionList() {
  const pathname = usePathname();
  const router = useRouter();
  const { sessions, loadSessions, activeSessionId, setActiveSession, removeSession } =
    useChatStore();
  const { chat: chatRepo } = useRepositories();
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    loadSessions(chatRepo);
  }, [chatRepo, loadSessions]);

  const handleDelete = async (sessionId: string) => {
    if (deletingId) return;
    setDeletingId(sessionId);
    try {
      await chatRepo.deleteSession(sessionId);
      removeSession(sessionId);
      if (activeSessionId === sessionId || pathname.includes(sessionId)) {
        setActiveSession(null);
        router.push("/chat");
      }
    } finally {
      setDeletingId(null);
    }
  };

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
        const isActive =
          pathname.includes(s.id) || (activeSessionId === s.id && pathname === "/chat");
        const { position, name } = splitEmployeeTitle(s.title);
        return (
          <div
            key={s.id}
            className={cn(
              "group flex items-center gap-1 rounded-md px-2 py-1.5 text-sm transition-colors",
              isActive
                ? "bg-secondary text-foreground font-medium"
                : "text-muted-foreground hover:bg-surface-hover hover:text-foreground",
            )}
          >
            <Link
              href={`/chat/${s.id}`}
              onClick={() => setActiveSession(s.id)}
              className="flex items-center gap-2 min-w-0 flex-1"
            >
              <MessageSquare className="h-3.5 w-3.5 shrink-0" />
              {position && (
                <Badge variant="secondary" className="text-[10px] px-1.5 py-0 shrink-0">
                  {position}
                </Badge>
              )}
              <span className="truncate">{name}</span>
            </Link>
            <button
              type="button"
              aria-label={`删除对话 ${s.title}`}
              onClick={() => handleDelete(s.id)}
              disabled={deletingId === s.id}
              className={cn(
                "shrink-0 rounded p-0.5 text-muted-foreground hover:text-destructive",
                "opacity-0 group-hover:opacity-100 focus:opacity-100",
                deletingId === s.id && "animate-pulse",
              )}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
