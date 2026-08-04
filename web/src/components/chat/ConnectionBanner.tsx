"use client";

import { WifiOff, Wifi, AlertTriangle } from "lucide-react";
import { useChatStore } from "@/lib/stores/chat-store";
import { Button } from "@/components/ui/button";

export function ConnectionBanner() {
  const { showConnectionBanner, setShowConnectionBanner, chatState } = useChatStore();

  if (!showConnectionBanner && chatState !== "reconnecting") return null;

  const isReconnecting = chatState === "reconnecting";
  const isError = chatState === "error";

  return (
    <div className="flex items-center justify-between px-4 py-2 bg-destructive/10 border-b border-destructive/20 text-sm">
      <div className="flex items-center gap-2">
        {isReconnecting ? (
          <WifiOff className="h-4 w-4 text-destructive animate-pulse" />
        ) : (
          <AlertTriangle className="h-4 w-4 text-destructive" />
        )}
        <span className="text-foreground">
          {isReconnecting
            ? "连接已断开，正在重连..."
            : "发生错误，请检查网络连接"}
        </span>
      </div>
      {isError && (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setShowConnectionBanner(false);
            useChatStore.setState({ chatState: "idle" });
          }}
        >
          <Wifi className="h-3 w-3 mr-1" />
          重试
        </Button>
      )}
    </div>
  );
}
