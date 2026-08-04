"use client";

import type { PlanningCategory } from "@/lib/domain/types";
import { cn } from "@/lib/utils";

const FILTERS: { key: PlanningCategory; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "memory", label: "记忆" },
  { key: "interest", label: "兴趣" },
  { key: "task", label: "任务" },
  { key: "skill", label: "Skill" },
];

interface PlanningFiltersProps {
  active: PlanningCategory;
  onChange: (c: PlanningCategory) => void;
}

export function PlanningFilters({ active, onChange }: PlanningFiltersProps) {
  return (
    <div className="flex items-center gap-1">
      {FILTERS.map(({ key, label }) => (
        <button
          key={key}
          type="button"
          onClick={() => onChange(key)}
          className={cn(
            "px-3 py-1.5 text-xs rounded-md transition-colors",
            active === key
              ? "bg-brand text-brand-foreground font-medium"
              : "text-muted-foreground hover:bg-surface-hover hover:text-foreground",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
