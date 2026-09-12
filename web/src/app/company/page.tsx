"use client";

import { AppShell } from "@/components/layout/AppShell";
import { CompanyList } from "@/components/company/CompanyList";

export default function CompanyPage() {
  return (
    <AppShell>
      <div className="h-full overflow-y-auto px-4 py-6 max-w-3xl mx-auto">
        <CompanyList />
      </div>
    </AppShell>
  );
}
