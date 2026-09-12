import { afterEach, describe, expect, it, vi } from "vitest";
import { createRealCompanyRepository } from "@/lib/api/real-company-repository";
import { PlanningApiError } from "@/lib/api/real-planning-repository";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const EMPLOYEE_ROW = {
  id: "emp-1",
  name: "李雷",
  position: "运营专员",
  prompt: "负责社区运营",
  status: "active",
  createdAt: "2026-08-06T00:00:00Z",
  updatedAt: "2026-08-06T00:00:00Z",
};

describe("RealCompanyRepository", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("listEmployees GETs the employees route and maps the rows", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([EMPLOYEE_ROW]));
    vi.stubGlobal("fetch", fetchMock);

    const employees = await createRealCompanyRepository().listEmployees();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/internal/v1/employees");
    expect(employees[0]).toEqual({
      id: "emp-1",
      name: "李雷",
      position: "运营专员",
      prompt: "负责社区运营",
      status: "active",
      createdAt: "2026-08-06T00:00:00Z",
      updatedAt: "2026-08-06T00:00:00Z",
    });
  });

  it("createEmployee POSTs the fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(EMPLOYEE_ROW));
    vi.stubGlobal("fetch", fetchMock);

    await createRealCompanyRepository().createEmployee({
      name: "李雷",
      position: "运营专员",
      prompt: "负责社区运营",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/internal/v1/employees",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ name: "李雷", position: "运营专员", prompt: "负责社区运营" }),
      }),
    );
  });

  it("updateEmployee PUTs the edit", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ ...EMPLOYEE_ROW, position: "产品经理" }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await createRealCompanyRepository().updateEmployee("emp-1", {
      position: "产品经理",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/internal/v1/employees/emp-1",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ position: "产品经理" }),
      }),
    );
    expect(result.position).toBe("产品经理");
  });

  it("requestEmployeeAction POSTs the approval route with position for adjust", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        approvalId: "app-1",
        employeeId: "emp-1",
        action: "adjust_position",
        name: "李雷",
        position: "运营专员",
        status: "pending",
        positionToSet: "产品经理",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const approval = await createRealCompanyRepository().requestEmployeeAction({
      employeeId: "emp-1",
      action: "adjust_position",
      position: "产品经理",
      idempotencyKey: "key-1",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/internal/v1/employees/approvals",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          employeeId: "emp-1",
          action: "adjust_position",
          position: "产品经理",
          idempotencyKey: "key-1",
        }),
      }),
    );
    expect(approval.id).toBe("app-1");
    expect(approval.status).toBe("pending");
    expect(approval.positionToSet).toBe("产品经理");
  });

  it("requestEmployeeAction omits position for fire/rehire", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        approvalId: "app-2",
        employeeId: "emp-1",
        action: "fire",
        name: "李雷",
        position: "运营专员",
        status: "pending",
        positionToSet: null,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createRealCompanyRepository().requestEmployeeAction({
      employeeId: "emp-1",
      action: "fire",
      idempotencyKey: "key-2",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/internal/v1/employees/approvals",
      expect.objectContaining({
        body: JSON.stringify({ employeeId: "emp-1", action: "fire", idempotencyKey: "key-2" }),
      }),
    );
  });

  it("decideEmployeeApproval POSTs the decision and maps the result employee", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        approvalId: "app-1",
        decision: "confirmed",
        employee: { ...EMPLOYEE_ROW, status: "inactive" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createRealCompanyRepository().decideEmployeeApproval(
      "app-1",
      "approve",
      "decide-1",
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/internal/v1/employees/approvals/app-1/decisions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ decision: "approve", idempotency_key: "decide-1" }),
      }),
    );
    expect(result.decision).toBe("confirmed");
    expect(result.employee?.status).toBe("inactive");
  });

  it("rejects with a typed error carrying the envelope code", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        { error: { code: "APPROVAL_CONFLICT", message: "Already decided." } },
        409,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const promise = createRealCompanyRepository().decideEmployeeApproval(
      "app-1",
      "approve",
      "decide-2",
    );
    await expect(promise).rejects.toBeInstanceOf(PlanningApiError);
    await expect(promise).rejects.toMatchObject({ code: "APPROVAL_CONFLICT" });
  });
});
