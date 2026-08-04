"use client";

import { useEffect, use } from "react";
import { useChatStore } from "@/lib/stores/chat-store";
import { useRepositories } from "@/lib/providers/repository-context";
import { AppShell } from "@/components/layout/AppShell";
import { SessionHeader } from "@/components/chat/SessionHeader";
import { MessageList } from "@/components/chat/MessageList";
import { Composer } from "@/components/chat/Composer";
import { ConnectionBanner } from "@/components/chat/ConnectionBanner";
import { PlanDraftCard } from "@/components/approval/PlanDraftCard";
import { Loader2 } from "lucide-react";

export default function ChatSessionPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = use(params);
  const {
    activeSessionId,
    setActiveSession,
    loadMessages,
    sessions,
    messages,
    drafts,
  } = useChatStore();
  const { chat: chatRepo } = useRepositories();

  useEffect(() => {
    setActiveSession(sessionId);
    loadMessages(chatRepo, sessionId);
  }, [sessionId, chatRepo, setActiveSession, loadMessages]);

  const session = sessions.find((s) => s.id === sessionId);
  const sessionDrafts = drafts.filter((d) => d.sessionId === sessionId);

  if (!session && sessions.length === 0) {
    return (
      <AppShell>
        <div className="flex items-center justify-center h-full">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="flex flex-col h-full">
        <SessionHeader title={session?.title ?? "对话"} />
        <ConnectionBanner />
        <MessageList />

        {/* Plan draft cards */}
        {sessionDrafts.length > 0 && (
          <div className="border-t border-border bg-surface/50 py-2 space-y-2">
            {sessionDrafts.map((draft) => (
              <PlanDraftCard key={draft.id} draft={draft} />
            ))}
          </div>
        )}

        <Composer />
      </div>
    </AppShell>
  );
}
