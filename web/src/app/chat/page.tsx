"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useChatStore } from "@/lib/stores/chat-store";
import { useRepositories } from "@/lib/providers/repository-context";
import { AppShell } from "@/components/layout/AppShell";
import { MessageList } from "@/components/chat/MessageList";
import { Composer } from "@/components/chat/Composer";
import { ConnectionBanner } from "@/components/chat/ConnectionBanner";

export default function ChatPage() {
  const router = useRouter();
  const { activeSessionId, loadSessions, sessions, setActiveSession } = useChatStore();
  const { chat: chatRepo } = useRepositories();

  useEffect(() => {
    async function init() {
      const allSessions = await chatRepo.listSessions();
      if (allSessions.length > 0) {
        router.replace(`/chat/${allSessions[0].id}`);
      }
    }
    init();
  }, [chatRepo, router]);

  return (
    <AppShell>
      <div className="flex flex-col h-full">
        <ConnectionBanner />
        <MessageList />
        <Composer />
      </div>
    </AppShell>
  );
}
