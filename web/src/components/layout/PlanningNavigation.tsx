"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const PLANNING_LINKS = [
  { href: "/planning", label: "全部" },
  { href: "/planning?category=memory", label: "记忆" },
  { href: "/planning?category=interest", label: "兴趣" },
  { href: "/planning?category=task", label: "任务" },
  { href: "/planning?category=skill", label: "Skill" },
];

export function PlanningNavigation() {
  const pathname = usePathname();

  return (
    <nav className="flex items-center gap-1 px-4 py-2 border-b border-border">
      {PLANNING_LINKS.map(({ href, label }) => {
        const isActive = pathname === href.split("?")[0];
        return (
          <Link
            key={href}
            href={href}
            className={cn(
              "px-3 py-1.5 text-xs rounded-md transition-colors",
              isActive
                ? "bg-secondary text-foreground font-medium"
                : "text-muted-foreground hover:text-foreground hover:bg-surface-hover",
            )}
          >
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
