"use client";

import { useState, useRef, useCallback } from "react";
import { Send, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useChatStore } from "@/lib/stores/chat-store";
import { useRepositories } from "@/lib/providers/repository-context";
import { generateRequestId } from "@/lib/utils/id";
import type { ChatEvent } from "@/lib/domain/types";

export function Composer() {
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const {
    activeSessionId,
    chatState,
    setChatState,
    addUserMessage,
    messages,
    sessions,
    addSession,
    addDraft,
  } = useChatStore();

  const { chat: chatRepo } = useRepositories();

  const isStreaming = chatState === "streaming" || chatState === "connecting";
  const canSend = input.trim().length > 0 && !sending && !isStreaming;

  const handleSend = useCallback(async () => {
    const content = input.trim();
    if (!content) return;

    let sessionId = activeSessionId;

    // Create new session if needed
    if (!sessionId) {
      const session = await chatRepo.createSession();
      addSession(session);
      sessionId = session.id;
      useChatStore.setState({ activeSessionId: sessionId });
    }

    setInput("");
    setSending(true);

    // Add user message locally
    addUserMessage(content);

    // Start streaming
    setChatState("connecting");
    const requestId = generateRequestId();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const stream = chatRepo.sendMessage(sessionId, content, requestId);

      for await (const event of stream) {
        if (controller.signal.aborted) break;
        handleChatEvent(event);
      }
    } catch {
      setChatState("error");
    } finally {
      setSending(false);
    }
  }, [
    input, activeSessionId, chatRepo, addUserMessage, setChatState,
    addSession, messages, sessions,
  ]);

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
    setChatState("completed");
    setSending(false);
  }, [setChatState]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (canSend) handleSend();
    }
  };

  // Handle streaming events
  function handleChatEvent(event: ChatEvent) {
    const state = useChatStore.getState();

    switch (event.type) {
      case "message-start":
        setChatState("streaming");
        break;

      case "token":
        // Append token to the last assistant message
        if (event.messageId) {
          const msgs = state.messages;
          const lastIdx = msgs.length - 1;
          if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
            const updated = [...msgs];
            updated[lastIdx] = {
              ...updated[lastIdx],
              content: updated[lastIdx].content + event.text,
              status: "streaming",
            };
            useChatStore.setState({ messages: updated });
          } else {
            // First token — create new assistant message
            const newMsg = {
              id: event.messageId,
              sessionId: activeSessionId ?? "",
              role: "assistant" as const,
              content: event.text,
              status: "streaming" as const,
              createdAt: new Date().toISOString(),
            };
            useChatStore.setState({ messages: [...msgs, newMsg] });
          }
        }
        break;

      case "draft":
        addDraft(event.draft);
        break;

      case "done":
        setChatState("completed");
        // Mark assistant message as completed
        useChatStore.setState((s) => ({
          messages: s.messages.map((m) =>
            m.id === event.messageId ? { ...m, status: "completed" as const } : m,
          ),
        }));
        break;

      case "error":
        setChatState("error");
        break;
    }
  }

  return (
    <div className="border-t border-border bg-background px-4 py-3">
      <div className="flex items-end gap-2 max-w-3xl mx-auto">
        <Textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入你的需求..."
          rows={1}
          className="min-h-[40px] max-h-[160px] resize-none"
          disabled={isStreaming}
        />
        {isStreaming ? (
          <Button
            variant="outline"
            size="icon"
            onClick={handleStop}
            aria-label="停止生成"
            className="shrink-0"
          >
            <Square className="h-4 w-4" />
          </Button>
        ) : (
          <Button
            variant="default"
            size="icon"
            onClick={handleSend}
            disabled={!canSend}
            aria-label="发送消息"
            className="shrink-0"
          >
            <Send className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
