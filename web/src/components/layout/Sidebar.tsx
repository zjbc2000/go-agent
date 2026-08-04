"use client";

import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
import { Plus, MessageSquare, Compass, Settings, PanelLeftClose, PanelLeft } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ScrollArea } from "@/components/ui/scroll-area";
import { GoudanLogo } from "@/components/brand/GoudanLogo";
import { SessionList } from "@/components/chat/SessionList";
import { useUIStore } from "@/lib/stores/ui-store";

const NAV_ITEMS = [
  { href: "/chat", label: "对话", icon: MessageSquare },
  { href: "/planning", label: "规划", icon: Compass },
  { href: "/settings", label: "设置", icon: Settings },
];

const iconBtnClass =
  "inline-flex items-center justify-center shrink-0 rounded-md h-9 w-9 text-muted-foreground hover:bg-secondary hover:text-foreground transition-colors";

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { sidebarOpen, toggleSidebar } = useUIStore();

  if (!sidebarOpen) {
    return (
      <aside className="flex flex-col items-center gap-3 w-14 h-full border-r border-border bg-sidebar py-3">
        <Tooltip>
          <TooltipTrigger
            className={iconBtnClass}
            aria-label="展开侧栏"
          >
            <PanelLeft className="h-5 w-5" />
          </TooltipTrigger>
          <TooltipContent side="right">展开侧栏</TooltipContent>
        </Tooltip>
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <Tooltip key={href}>
              <TooltipTrigger
                className={cn(
                  iconBtnClass,
                  active && "bg-secondary text-foreground",
                )}
                aria-label={label}
                onClick={() => router.push(href)}
              >
                <Icon className="h-5 w-5" />
              </TooltipTrigger>
              <TooltipContent side="right">{label}</TooltipContent>
            </Tooltip>
          );
        })}
      </aside>
    );
  }

  return (
    <aside className="flex flex-col w-64 h-full border-r border-border bg-sidebar">
      {/* Logo */}
      <div className="flex items-center justify-between px-4 h-14 border-b border-border shrink-0">
        <Link href="/chat">
          <GoudanLogo size={28} />
        </Link>
        <Tooltip>
          <TooltipTrigger
            className={iconBtnClass}
            aria-label="收起侧栏"
          >
            <PanelLeftClose className="h-4 w-4" />
          </TooltipTrigger>
          <TooltipContent side="right">收起侧栏</TooltipContent>
        </Tooltip>
      </div>

      {/* New Chat */}
      <div className="px-3 pt-3">
        <Link href="/chat">
          <Button variant="outline" className="w-full justify-start gap-2" size="sm">
            <Plus className="h-4 w-4" />
            新建对话
          </Button>
        </Link>
      </div>

      {/* Navigation */}
      <nav className="px-3 py-2 space-y-1">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <Link key={href} href={href}>
              <Button
                variant={active ? "secondary" : "ghost"}
                className={cn("w-full justify-start gap-2", active && "font-medium")}
                size="sm"
              >
                <Icon className="h-4 w-4" />
                {label}
              </Button>
            </Link>
          );
        })}
      </nav>

      <div className="mx-3 my-2">
        <div className="h-px bg-border" />
      </div>

      {/* Session list */}
      <ScrollArea className="flex-1 px-3">
        <SessionList />
      </ScrollArea>
    </aside>
  );
}
