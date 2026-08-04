"use client";

import { MessageSquare } from "lucide-react";

export function EmptyConversation() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-muted-foreground">
      <div className="flex items-center justify-center w-16 h-16 rounded-full bg-surface border border-border">
        <MessageSquare className="h-8 w-8" />
      </div>
      <div className="text-center space-y-1">
        <p className="text-sm font-medium text-foreground">开始新对话</p>
        <p className="text-xs">
          输入你的需求，苟蛋会帮你规划、记录和执行。
        </p>
      </div>
    </div>
  );
}
