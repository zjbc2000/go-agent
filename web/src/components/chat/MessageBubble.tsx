"use client";

import type { Message } from "@/lib/domain/types";
import { cn } from "@/lib/utils";
import { GoudanLogo } from "@/components/brand/GoudanLogo";
import { User, AlertCircle } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MessageBubbleProps {
  message: Message;
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";

  return (
    <div
      className={cn(
        "flex gap-3 px-4 py-3",
        isUser ? "justify-end" : "justify-start",
      )}
    >
      {/* Avatar */}
      {!isUser && (
        <div className="shrink-0 mt-0.5">
          <GoudanLogo size={24} showText={false} />
        </div>
      )}

      <div
        className={cn(
          "max-w-[75%] rounded-md px-3 py-2 text-sm leading-relaxed",
          isUser
            ? "bg-brand text-brand-foreground"
            : "bg-surface border border-border text-foreground",
        )}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : (
          <div className="prose prose-sm dark:prose-invert max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.content}
            </ReactMarkdown>
          </div>
        )}

        {/* Error indicator */}
        {message.status === "error" && (
          <div className="flex items-center gap-1 mt-1 text-xs text-destructive">
            <AlertCircle className="h-3 w-3" />
            {message.error ?? "发送失败"}
          </div>
        )}
      </div>

      {/* User avatar */}
      {isUser && (
        <div className="shrink-0 mt-0.5 flex items-center justify-center w-6 h-6 rounded-full bg-secondary">
          <User className="h-3.5 w-3.5 text-muted-foreground" />
        </div>
      )}
    </div>
  );
}
