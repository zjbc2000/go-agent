// ============================================================
// Real Company Repository — BFF-backed employees and HITL action
// approvals. Reuses the exported planning error helpers; all calls are
// same-origin BFF paths (/api/v1/internal/v1/*).
// ============================================================

import type { CompanyRepository } from "@/lib/domain/repositories";
import type {
  CreateEmployeeInput,
  Employee,
  EmployeeActionApproval,
  EmployeeDecisionResult,
  UpdateEmployeeInput,
} from "@/lib/domain/types";
import { toPlanningError } from "./real-planning-repository";

const COMPANY_API = "/api/v1/internal/v1";

interface EmployeeRow {
  id: string;
  name: string;
  position: string;
  prompt: string;
  status: "active" | "inactive";
  createdAt: string;
  updatedAt: string;
}

interface ApprovalRow {
  approvalId: string;
  employeeId: string;
  action: EmployeeActionApproval["action"];
  name: string;
  position: string | null;
  status: string;
  positionToSet: string | null;
}

interface DecisionRow {
  approvalId: string;
  decision: string;
  employee: EmployeeRow | null;
}

function toEmployee(row: EmployeeRow): Employee {
  return {
    id: row.id,
    name: row.name,
    position: row.position,
    prompt: row.prompt,
    status: row.status,
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
  };
}

function toApproval(row: ApprovalRow): EmployeeActionApproval {
  return {
    id: row.approvalId,
    employeeId: row.employeeId,
    action: row.action,
    name: row.name,
    position: row.position,
    positionToSet: row.positionToSet,
    status: "pending",
    createdAt: new Date().toISOString(),
  };
}

export function createRealCompanyRepository(): CompanyRepository {
  return {
    async listEmployees(): Promise<Employee[]> {
      const res = await fetch(`${COMPANY_API}/employees`);
      if (!res.ok) throw toPlanningError(res.status, await res.json().catch(() => null));
      const rows = (await res.json()) as EmployeeRow[];
      return rows.map(toEmployee);
    },

    async createEmployee(input: CreateEmployeeInput): Promise<Employee> {
      const res = await fetch(`${COMPANY_API}/employees`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      });
      if (!res.ok) throw toPlanningError(res.status, await res.json().catch(() => null));
      return toEmployee((await res.json()) as EmployeeRow);
    },

    async updateEmployee(id: string, input: UpdateEmployeeInput): Promise<Employee> {
      const res = await fetch(`${COMPANY_API}/employees/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      });
      if (!res.ok) throw toPlanningError(res.status, await res.json().catch(() => null));
      return toEmployee((await res.json()) as EmployeeRow);
    },

    async requestEmployeeAction(input: {
      employeeId: string;
      action: EmployeeActionApproval["action"];
      position?: string;
      idempotencyKey: string;
    }): Promise<EmployeeActionApproval> {
      const res = await fetch(`${COMPANY_API}/employees/approvals`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          employeeId: input.employeeId,
          action: input.action,
          ...(input.position !== undefined ? { position: input.position } : {}),
          idempotencyKey: input.idempotencyKey,
        }),
      });
      if (!res.ok) throw toPlanningError(res.status, await res.json().catch(() => null));
      return toApproval((await res.json()) as ApprovalRow);
    },

    async decideEmployeeApproval(
      approvalId: string,
      decision: "approve" | "reject",
      idempotencyKey: string,
    ): Promise<EmployeeDecisionResult> {
      const res = await fetch(`${COMPANY_API}/employees/approvals/${approvalId}/decisions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, idempotency_key: idempotencyKey }),
      });
      if (!res.ok) throw toPlanningError(res.status, await res.json().catch(() => null));
      const row = (await res.json()) as DecisionRow;
      return {
        approvalId: row.approvalId,
        decision: row.decision,
        employee: row.employee ? toEmployee(row.employee) : null,
      };
    },
  };
}
