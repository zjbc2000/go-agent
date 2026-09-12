import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createRealPlanningRepository,
  PlanningApiError,
} from "@/lib/api/real-planning-repository";
import type { ApprovalDecision } from "@/lib/domain/types";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("RealPlanningRepository", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("decideApproval POSTs the backend envelope with edited_payload and maps the result", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        approvalId: "app-1",
        decision: "confirmed",
        version: 2,
        originalPayload: { type: "task", title: "original", body: "original body" },
        document: { id: "doc-1", type: "task", title: "edited", body: "body", version: 2 },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createRealPlanningRepository().decideApproval({
      approvalId: "app-1",
      decision: "confirm",
      editedPayload: { title: "edited", content: "body" },
      idempotencyKey: "req-1",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/internal/v1/approvals/app-1/decisions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          decision: "approve",
          edited_payload: { title: "edited", body: "body" },
          idempotency_key: "req-1",
        }),
      }),
    );
    expect(result.document).toEqual({
      id: "doc-1",
      title: "edited",
      content: "body",
      category: "task",
      version: 2,
    });
    expect(result.originalPayload).toEqual({
      title: "original",
      content: "original body",
      category: "task",
    });
  });

  it("maps frontend confirm/reject/regenerate to backend decisions", async () => {
    const cases: Array<[ApprovalDecision, string]> = [
      ["confirm", "approve"],
      ["reject", "reject"],
      ["regenerate", "regenerate"],
    ];
    for (const [frontend, backend] of cases) {
      const fetchMock = vi.fn().mockResolvedValue(
        jsonResponse({ approvalId: "app-x", decision: "confirmed", version: null }),
      );
      vi.stubGlobal("fetch", fetchMock);
      await createRealPlanningRepository().decideApproval({
        approvalId: "app-x",
        decision: frontend,
        idempotencyKey: "req-x",
      });
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/internal/v1/approvals/app-x/decisions",
        expect.objectContaining({
          body: JSON.stringify({ decision: backend, edited_payload: null, idempotency_key: "req-x" }),
        }),
      );
    }
  });

  it("decideApproval rejects with a typed error carrying the envelope code", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ error: { code: "APPROVAL_EXPIRED", message: "Approval has expired." } }, 409),
    );
    vi.stubGlobal("fetch", fetchMock);

    const promise = createRealPlanningRepository().decideApproval({
      approvalId: "app-3",
      decision: "confirm",
      idempotencyKey: "req-3",
    });
    await expect(promise).rejects.toBeInstanceOf(PlanningApiError);
    await expect(promise).rejects.toMatchObject({ code: "APPROVAL_EXPIRED" });
  });

  it("listDocuments GETs documents and maps body->content and type->category", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse([
        {
          id: "doc-1",
          type: "task",
          title: "T",
          body: "B",
          version: 1,
          createdAt: "2026-01-01T00:00:00Z",
          updatedAt: "2026-01-02T00:00:00Z",
        },
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const docs = await createRealPlanningRepository().listDocuments();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/internal/v1/documents");
    expect(docs[0]).toEqual({
      id: "doc-1",
      title: "T",
      content: "B",
      category: "task",
      version: 1,
      createdAt: "2026-01-01T00:00:00Z",
      updatedAt: "2026-01-02T00:00:00Z",
    });
  });

  it("listDocuments sends the type query param for a specific category", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await createRealPlanningRepository().listDocuments({ category: "memory" });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/internal/v1/documents?type=memory");
  });

  it("listVersions maps version rows body->content", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse([
        {
          id: "ver-1",
          documentId: "doc-1",
          version: 1,
          title: "T",
          body: "B",
          createdAt: "2026-01-01T00:00:00Z",
        },
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const versions = await createRealPlanningRepository().listVersions("doc-1");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/internal/v1/documents/doc-1/versions");
    expect(versions[0]).toEqual({
      id: "ver-1",
      documentId: "doc-1",
      version: 1,
      title: "T",
      content: "B",
      createdAt: "2026-01-01T00:00:00Z",
    });
  });

  it("restoreVersion POSTs the restore path", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ id: "doc-1", type: "task", title: "T", body: "B", version: 3 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createRealPlanningRepository().restoreVersion("doc-1", "ver-1");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/internal/v1/documents/doc-1/versions/ver-1/restore",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("updateDocument PUTs the edit and maps the returned document", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        id: "doc-1",
        type: "task",
        title: "T2",
        body: "B2",
        version: 2,
        createdAt: "2026-08-06T00:00:00Z",
        updatedAt: "2026-08-06T00:00:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createRealPlanningRepository().updateDocument("doc-1", {
      title: "T2",
      content: "B2",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/internal/v1/documents/doc-1",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ title: "T2", body: "B2" }) }),
    );
    expect(result).toMatchObject({ id: "doc-1", title: "T2", content: "B2", version: 2 });
  });

  it("deleteVersion DELETEs the version route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await createRealPlanningRepository().deleteVersion("doc-1", "ver-3");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/internal/v1/documents/doc-1/versions/ver-3",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("requestExecution POSTs the executions route and maps the approval envelope", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        approval: {
          id: "app-1",
          status: "pending",
          expiresAt: "2026-01-01T00:00:00Z",
          createdAt: "2026-01-01T00:00:00Z",
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createRealPlanningRepository().requestExecution(
      "doc-1",
      { title: "x" },
      "key-1",
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/internal/v1/skills/doc-1/executions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ inputs: { title: "x" }, idempotency_key: "key-1" }),
      }),
    );
    expect(result).toEqual({
      kind: "approval",
      approval: {
        approvalId: "app-1",
        status: "pending",
        expiresAt: "2026-01-01T00:00:00Z",
        createdAt: "2026-01-01T00:00:00Z",
      },
    });
  });

  it("requestExecution maps the run envelope", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ run: { id: "run-1", status: "queued", planHash: "abc123" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createRealPlanningRepository().requestExecution("doc-1", {}, "key-2");

    expect(result).toEqual({
      kind: "run",
      run: { runId: "run-1", status: "queued", planHash: "abc123" },
    });
  });

  it("requestExecution rejects with a typed error carrying the envelope code", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ error: { code: "SKILL_INVALID", message: "Not a skill." } }, 422),
    );
    vi.stubGlobal("fetch", fetchMock);

    const promise = createRealPlanningRepository().requestExecution("doc-1", {}, "key-3");
    await expect(promise).rejects.toBeInstanceOf(PlanningApiError);
    await expect(promise).rejects.toMatchObject({ code: "SKILL_INVALID" });
  });

  it("decideExecutionApproval POSTs the decisions route and maps the error code", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        { error: { code: "APPROVAL_EXPIRED", message: "Execution approval has expired.", retryable: true } },
        409,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createRealPlanningRepository().decideExecutionApproval(
      "app-1",
      "approve",
      "key-4",
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/internal/v1/skills/approvals/app-1/decisions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ decision: "approve", idempotency_key: "key-4" }),
      }),
    );
    expect(result.decision).toBe("error");
    expect(result.error?.code).toBe("APPROVAL_EXPIRED");
    expect(result.error?.retryable).toBe(true);
  });

  it("decideExecutionApproval maps a successful confirm to the run", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ decision: "confirmed", run: { id: "run-1", status: "queued", planHash: "abc" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createRealPlanningRepository().decideExecutionApproval(
      "app-1",
      "approve",
      "key-5",
    );

    expect(result.decision).toBe("confirmed");
    expect(result.run).toEqual({ runId: "run-1", status: "queued", planHash: "abc" });
  });
});
