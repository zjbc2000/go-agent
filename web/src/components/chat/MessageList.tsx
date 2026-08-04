"use client";

import { useEffect, useRef, useCallback } from "react";
import { useChatStore } from "@/lib/stores/chat-store";
import { MessageBubble } from "./MessageBubble";
import { StreamingMessage } from "./StreamingMessage";
import { EmptyConversation } from "./EmptyConversation";
import { ArrowDown } from "lucide-react";
import { Button } from "@/components/ui/button";

export function MessageList() {
  const { messages, chatState, userScrolledUp, setUserScrolledUp } = useChatStore();
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  // Track scroll position
  const handleScroll = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const { scrollTop, scrollHeight, clientHeight } = container;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 80;
    setUserScrolledUp(!isAtBottom);
  }, [setUserScrolledUp]);

  // Auto-scroll when streaming (unless user scrolled up)
  useEffect(() => {
    if (!userScrolledUp && chatState === "streaming") {
      scrollToBottom();
    }
  }, [messages, chatState, userScrolledUp, scrollToBottom]);

  const isStreaming = chatState === "streaming" || chatState === "connecting";

  if (messages.length === 0 && chatState === "idle") {
    return <EmptyConversation />;
  }

  return (
    <div className="relative flex-1 overflow-hidden">
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="h-full overflow-y-auto"
      >
        <div className="py-4">
          {messages.map((msg) => (
            <MessageBubble key={msg.id} message={msg} />
          ))}

          {/* Streaming placeholder */}
          {isStreaming && <StreamingMessage />}
        </div>
        <div ref={bottomRef} />
      </div>

      {/* Scroll-to-bottom button */}
      {userScrolledUp && (
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2">
          <Button
            variant="secondary"
            size="sm"
            onClick={scrollToBottom}
            className="shadow-md"
          >
            <ArrowDown className="h-4 w-4 mr-1" />
            回到最新消息
          </Button>
        </div>
      )}
    </div>
  );
}
