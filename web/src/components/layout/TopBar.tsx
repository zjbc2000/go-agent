"use client";

import { usePathname } from "next/navigation";
import { Menu } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useUIStore } from "@/lib/stores/ui-store";

const PAGE_TITLES: Record<string, string> = {
  "/chat": "对话",
  "/planning": "规划",
  "/settings": "设置",
  "/login": "登录",
};

export function TopBar() {
  const pathname = usePathname();
  const { toggleSidebar } = useUIStore();

  const title = PAGE_TITLES[pathname] ??
    Object.entries(PAGE_TITLES).find(([k]) => pathname.startsWith(k))?.[1] ??
    "苟蛋";

  return (
    <header className="flex items-center gap-3 h-14 px-4 border-b border-border bg-background shrink-0">
      <Button
        variant="ghost"
        size="icon"
        onClick={toggleSidebar}
        aria-label="切换侧栏"
      >
        <Menu className="h-5 w-5" />
      </Button>
      <h1 className="text-sm font-semibold text-foreground">{title}</h1>
    </header>
  );
}
