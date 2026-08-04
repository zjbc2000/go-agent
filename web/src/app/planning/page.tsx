"use client";

import { AppShell } from "@/components/layout/AppShell";
import { PlanningList } from "@/components/planning/PlanningList";

export default function PlanningPage() {
  return (
    <AppShell>
      <div className="h-full overflow-y-auto px-4 py-6 max-w-3xl mx-auto">
        <PlanningList />
      </div>
    </AppShell>
  );
}
