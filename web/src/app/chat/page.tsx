"use client";

import { useEffect } from "react";
import { useChatStore } from "@/lib/stores/chat-store";
import { useRepositories } from "@/lib/providers/repository-context";
import { AppShell } from "@/components/layout/AppShell";
import { MessageList } from "@/components/chat/MessageList";
import { Composer } from "@/components/chat/Composer";
import { ConnectionBanner } from "@/components/chat/ConnectionBanner";

export default function ChatPage() {
  const { loadSessions, setActiveSession } = useChatStore();
  const { chat: chatRepo } = useRepositories();

  useEffect(() => {
    // /chat is the "start new chat" page: load the session list for the sidebar but
    // clear any stale active session so the first message creates a fresh session.
    setActiveSession(null);
    loadSessions(chatRepo);
  }, [chatRepo, loadSessions, setActiveSession]);

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
