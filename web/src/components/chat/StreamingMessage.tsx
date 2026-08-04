"use client";

import { useChatStore } from "@/lib/stores/chat-store";
import { GoudanLogo } from "@/components/brand/GoudanLogo";

export function StreamingMessage() {
  const { chatState } = useChatStore();

  const label =
    chatState === "connecting" ? "连接中..." :
    chatState === "streaming" ? "思考中..." :
    chatState === "reconnecting" ? "重连中..." : "";

  return (
    <div className="flex gap-3 px-4 py-3">
      <div className="shrink-0 mt-0.5">
        <GoudanLogo size={24} showText={false} />
      </div>
      <div className="bg-surface border border-border rounded-md px-3 py-2 text-sm text-muted-foreground">
        <span className="inline-flex items-center gap-1">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-brand animate-pulse" />
          {label}
        </span>
      </div>
      {/* Spacer to match user avatar width */}
      <div className="w-6 shrink-0" />
    </div>
  );
}
