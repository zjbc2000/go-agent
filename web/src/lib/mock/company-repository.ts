// ============================================================
// Mock Company Repository — in-memory employees + HITL action approvals
// ============================================================

import type { CompanyRepository } from "@/lib/domain/repositories";
import type {
  CreateEmployeeInput,
  Employee,
  EmployeeActionApproval,
  EmployeeDecisionResult,
  UpdateEmployeeInput,
} from "@/lib/domain/types";
import { generateId } from "@/lib/utils/id";

// --- In-memory store ---

let employees: Employee[] = [
  {
    id: "emp_1",
    name: "李雷",
    position: "运营专员",
    prompt: "负责社区运营与活动策划，回复用户反馈，维护社群活跃度。",
    status: "active",
    createdAt: new Date(Date.now() - 12 * 86_400_000).toISOString(),
    updatedAt: new Date(Date.now() - 3 * 86_400_000).toISOString(),
  },
  {
    id: "emp_2",
    name: "韩梅梅",
    position: "设计师",
    prompt: "负责界面视觉与品牌设计，输出设计规范和物料。",
    status: "inactive",
    createdAt: new Date(Date.now() - 9 * 86_400_000).toISOString(),
    updatedAt: new Date(Date.now() - 1 * 86_400_000).toISOString(),
  },
];

let approvals: EmployeeActionApproval[] = [];

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function nowIso(): string {
  return new Date().toISOString();
}

// --- Implementation ---

export function createMockCompanyRepository(): CompanyRepository {
  return {
    async listEmployees(): Promise<Employee[]> {
      await delay(150);
      return [...employees];
    },

    async createEmployee(input: CreateEmployeeInput): Promise<Employee> {
      await delay(150);
      const employee: Employee = {
        id: `emp_${generateId("e")}`,
        name: input.name,
        position: input.position,
        prompt: input.prompt,
        status: "active",
        createdAt: nowIso(),
        updatedAt: nowIso(),
      };
      employees = [employee, ...employees];
      return employee;
    },

    async updateEmployee(id: string, input: UpdateEmployeeInput): Promise<Employee> {
      await delay(150);
      const current = employees.find((e) => e.id === id);
      if (!current) throw new Error(`Employee ${id} not found`);
      const updated: Employee = {
        ...current,
        ...input,
        updatedAt: nowIso(),
      };
      employees = employees.map((e) => (e.id === id ? updated : e));
      return updated;
    },

    async requestEmployeeAction(input: {
      employeeId: string;
      action: EmployeeActionApproval["action"];
      position?: string;
      idempotencyKey: string;
    }): Promise<EmployeeActionApproval> {
      await delay(100);
      const { idempotencyKey: key, ...rest } = input;
      const employee = employees.find((e) => e.id === rest.employeeId);
      if (!employee) throw new Error(`Employee ${rest.employeeId} not found`);

      const existing = approvals.find(
        (a) =>
          a.employeeId === rest.employeeId &&
          a.action === rest.action &&
          a.status === "pending",
      );
      if (existing) return existing;
      // Use the idempotency key as a natural dedupe: an identical repeat returns the
      // already-created pending approval.
      const byKey = approvals.find((a) => a.id === key && a.status === "pending");
      if (byKey) return byKey;

      const approval: EmployeeActionApproval = {
        id: `emp-appr-${generateId("a")}`,
        employeeId: employee.id,
        action: rest.action,
        name: employee.name,
        position: employee.position,
        positionToSet: rest.position ?? null,
        status: "pending",
        createdAt: nowIso(),
      };
      approvals = [...approvals, approval];
      return approval;
    },

    async decideEmployeeApproval(
      approvalId: string,
      decision: "approve" | "reject",
      idempotencyKey: string,
    ): Promise<EmployeeDecisionResult> {
      await delay(100);
      const approval = approvals.find((a) => a.id === approvalId);
      if (!approval) throw new Error(`Approval ${approvalId} not found`);
      if (approval.status !== "pending") {
        // Same approval decided with a different key = conflict, mirroring the backend.
        throw new Error("APPROVAL_CONFLICT");
      }
      void idempotencyKey; // key is accepted for API parity; the mock is state-machine simple

      let employee = employees.find((e) => e.id === approval.employeeId);
      if (!employee) throw new Error(`Employee ${approval.employeeId} not found`);

      if (decision === "approve") {
        if (approval.action === "fire") employee = { ...employee, status: "inactive" };
        else if (approval.action === "rehire") employee = { ...employee, status: "active" };
        else if (approval.action === "adjust_position" && approval.positionToSet) {
          employee = { ...employee, position: approval.positionToSet };
        }
        employee = { ...employee, updatedAt: nowIso() };
        employees = employees.map((e) => (e.id === employee!.id ? employee! : e));
        approvals = approvals.map((a) =>
          a.id === approvalId ? { ...a, status: "confirmed" } : a,
        );
        return {
          approvalId,
          decision: "confirmed",
          employee: { ...employee },
        };
      }

      approvals = approvals.map((a) =>
        a.id === approvalId ? { ...a, status: "rejected" } : a,
      );
      return { approvalId, decision: "rejected", employee: null };
    },
  };
}
