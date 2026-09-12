import { describe, it, expect, beforeEach } from "vitest";
import { createMockCompanyRepository } from "@/lib/mock/company-repository";

describe("MockCompanyRepository", () => {
  const repo = createMockCompanyRepository();

  beforeEach(async () => {
    // Reset module store between tests by re-seeding is not trivial (module-level
    // state); each test uses unique employee ids so assertions are self-contained.
  });

  it("lists seeded employees", async () => {
    const employees = await repo.listEmployees();
    expect(employees.length).toBeGreaterThanOrEqual(2);
    expect(employees.some((e) => e.name === "李雷")).toBe(true);
  });

  it("creates an employee with status defaulting to 在职", async () => {
    const created = await repo.createEmployee({
      name: "王小明",
      position: "工程师",
      prompt: "负责后端开发",
    });
    expect(created.status).toBe("active");
    expect(created.position).toBe("工程师");
    expect(created.id).toBeTruthy();
  });

  it("updates an employee's prompt", async () => {
    const created = await repo.createEmployee({
      name: "王小明",
      position: "工程师",
      prompt: "初版提示词",
    });
    const updated = await repo.updateEmployee(created.id, { prompt: "新版提示词" });
    expect(updated.prompt).toBe("新版提示词");
    expect(updated.name).toBe("王小明");
  });

  it("fires and rehires an employee through approvals", async () => {
    const created = await repo.createEmployee({
      name: "王小明",
      position: "工程师",
      prompt: "负责后端开发",
    });

    const fire = await repo.requestEmployeeAction({
      employeeId: created.id,
      action: "fire",
      idempotencyKey: "fire-1",
    });
    expect(fire.status).toBe("pending");

    const fired = await repo.decideEmployeeApproval(fire.id, "approve", "fire-decide-1");
    expect(fired.employee?.status).toBe("inactive");

    const rehire = await repo.requestEmployeeAction({
      employeeId: created.id,
      action: "rehire",
      idempotencyKey: "rehire-1",
    });
    const rehired = await repo.decideEmployeeApproval(rehire.id, "approve", "rehire-decide-1");
    expect(rehired.employee?.status).toBe("active");
  });

  it("adjusts an employee's position", async () => {
    const created = await repo.createEmployee({
      name: "王小明",
      position: "工程师",
      prompt: "负责后端开发",
    });
    const adjust = await repo.requestEmployeeAction({
      employeeId: created.id,
      action: "adjust_position",
      position: "技术经理",
      idempotencyKey: "adjust-1",
    });
    const result = await repo.decideEmployeeApproval(adjust.id, "approve", "adjust-decide-1");
    expect(result.employee?.position).toBe("技术经理");
  });

  it("throws when acting on a missing employee", async () => {
    await expect(
      repo.updateEmployee("nonexistent", { prompt: "x" }),
    ).rejects.toThrow();
    await expect(
      repo.requestEmployeeAction({ employeeId: "nonexistent", action: "fire", idempotencyKey: "k" }),
    ).rejects.toThrow();
  });

  it("conflicts when deciding an already-resolved approval with a new action", async () => {
    const created = await repo.createEmployee({
      name: "王小明",
      position: "工程师",
      prompt: "提示词",
    });
    const fire = await repo.requestEmployeeAction({
      employeeId: created.id,
      action: "fire",
      idempotencyKey: "fire-2",
    });
    await repo.decideEmployeeApproval(fire.id, "approve", "fire-decide-2");
    await expect(
      repo.decideEmployeeApproval(fire.id, "approve", "fire-decide-OTHER"),
    ).rejects.toThrow();
  });
});
