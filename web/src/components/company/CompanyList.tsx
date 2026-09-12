"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import type { Employee } from "@/lib/domain/types";
import { useRepositories } from "@/lib/providers/repository-context";
import { EmployeeCard } from "./EmployeeCard";
import { NewEmployeeDialog } from "./NewEmployeeDialog";
import { Button } from "@/components/ui/button";
import { Loader2, Users, Plus } from "lucide-react";

export function CompanyList() {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const { company: companyRepo } = useRepositories();
  // Refresh (e.g. after a save or approval) keeps the list mounted; only the initial
  // load shows the spinner.
  const hasLoadedRef = useRef(false);

  const loadEmployees = useCallback(async () => {
    if (!hasLoadedRef.current) setLoading(true);
    try {
      const rows = await companyRepo.listEmployees();
      setEmployees(rows);
      setError(null);
      hasLoadedRef.current = true;
    } catch {
      // Friendly text only — never surface raw error payloads.
      setError("加载员工失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  }, [companyRepo]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async data fetch; setState occurs after await, not synchronously
    loadEmployees();
  }, [loadEmployees]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium text-muted-foreground">员工</h2>
        <Button size="sm" onClick={() => setDialogOpen(true)}>
          <Plus className="h-4 w-4 mr-1" />
          新建员工
        </Button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center gap-2 py-12 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span className="text-sm">加载中...</span>
        </div>
      ) : error ? (
        <div className="flex items-center justify-center py-12 text-muted-foreground">
          <p className="text-sm">{error}</p>
        </div>
      ) : employees.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 py-12 text-muted-foreground">
          <Users className="h-8 w-8" />
          <p className="text-sm">暂无员工</p>
          <p className="text-xs">点击右上角「新建员工」创建你的第一位智能体员工</p>
        </div>
      ) : (
        <div className="grid gap-3">
          {employees.map((employee) => (
            <EmployeeCard
              key={employee.id}
              employee={employee}
              onUpdated={loadEmployees}
            />
          ))}
        </div>
      )}

      <NewEmployeeDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onCreated={() => {
          setDialogOpen(false);
          loadEmployees();
        }}
      />
    </div>
  );
}
