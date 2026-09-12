// ============================================================
// Real Planning Repository — BFF-backed planning documents, versions,
// and immutable approval decisions.
//
// All calls are same-origin BFF paths (/api/v1/internal/v1/*); the browser never
// references the agent-service host or port. The BFF authenticates the session and
// forwards the end-user JWT plus the internal service token to the agent service.
// ============================================================

import type { PlanningRepository } from "@/lib/domain/repositories";
import type {
  ApprovalDecisionInput,
  ApprovalResult,
  DocumentVersion,
  ExecutionApproval,
  ExecutionApprovalDecision,
  ExecutionDecisionResult,
  ExecutionStatus,
  PlanningDocument,
  PlanningFilter,
  SandboxRun,
  SkillExecution,
  UpdateDocumentInput,
} from "@/lib/domain/types";

const PLANNING_API = "/api/v1/internal/v1";

const CATEGORY_TYPES = new Set(["memory", "interest", "task", "skill"]);

/** Typed planning error carrying the BFF/backend envelope code. */
export class PlanningApiError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "PlanningApiError";
    this.code = code;
  }
}

/** Map the frontend decision to the backend decision vocabulary. */
function toBackendDecision(decision: ApprovalDecisionInput["decision"]): string {
  // Plain confirm and edit-confirm both land on the backend "approve".
  if (decision === "confirm") return "approve";
  return decision;
}

export function toPlanningError(status: number, errBody: unknown): PlanningApiError {
  const envelope = errBody as { error?: { code?: string; message?: string } } | null;
  const code = envelope?.error?.code ?? "INTERNAL_ERROR";
  const message = envelope?.error?.message ?? `Planning request failed: ${status}`;
  return new PlanningApiError(code, message);
}

interface DecisionEnvelope {
  approvalId: string;
  decision: string;
  version: number | null;
  originalPayload?: { type: string; title: string; body: string } | null;
  document?: { id: string; type: string; title: string; body: string; version: number } | null;
}

interface DocumentRow {
  id: string;
  type: string;
  title: string;
  body: string;
  version: number;
  createdAt: string;
  updatedAt: string;
}

interface VersionRow {
  id: string;
  documentId: string;
  version: number;
  title: string;
  body: string;
  createdAt: string;
}

/** The backend execution envelope: exactly one of approval or run is present. */
interface ExecutionEnvelope {
  approval?: {
    id: string;
    status: string;
    expiresAt: string;
    createdAt: string;
  };
  run?: { id: string; status: string; planHash?: string };
}

/** The backend execution-decision envelope. */
interface ExecutionDecisionEnvelope {
  decision?: string;
  run?: { id: string; status: string; planHash?: string };
}

function toExecutionApproval(row: NonNullable<ExecutionEnvelope["approval"]>): ExecutionApproval {
  return {
    approvalId: row.id,
    status: row.status as ExecutionApproval["status"],
    expiresAt: row.expiresAt,
    createdAt: row.createdAt,
  };
}

function toSandboxRun(row: NonNullable<ExecutionEnvelope["run"]>): SandboxRun {
  return {
    runId: row.id,
    status: row.status as ExecutionStatus,
    ...(row.planHash ? { planHash: row.planHash } : {}),
  };
}

export function createRealPlanningRepository(): PlanningRepository {
  return {
    async listDocuments(filter?: PlanningFilter): Promise<PlanningDocument[]> {
      const params = new URLSearchParams();
      if (filter?.category && CATEGORY_TYPES.has(filter.category)) {
        params.set("type", filter.category);
      }
      const query = params.toString();
      const res = await fetch(`${PLANNING_API}/documents${query ? `?${query}` : ""}`);
      if (!res.ok) throw toPlanningError(res.status, await res.json().catch(() => null));
      const rows = (await res.json()) as DocumentRow[];
      return rows.map((row) => ({
        id: row.id,
        title: row.title,
        content: row.body,
        category: row.type as PlanningDocument["category"],
        version: row.version,
        createdAt: row.createdAt,
        updatedAt: row.updatedAt,
      }));
    },

    async updateDocument(id: string, input: UpdateDocumentInput): Promise<PlanningDocument> {
      const res = await fetch(`${PLANNING_API}/documents/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(input.title !== undefined ? { title: input.title } : {}),
          ...(input.content !== undefined ? { body: input.content } : {}),
        }),
      });
      if (!res.ok) throw toPlanningError(res.status, await res.json().catch(() => null));
      const row = (await res.json()) as DocumentRow;
      return {
        id: row.id,
        title: row.title,
        content: row.body,
        category: row.type as PlanningDocument["category"],
        version: row.version,
        createdAt: row.createdAt,
        updatedAt: row.updatedAt,
      };
    },

    async decideApproval(input: ApprovalDecisionInput): Promise<ApprovalResult> {
      const res = await fetch(`${PLANNING_API}/approvals/${input.approvalId}/decisions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          decision: toBackendDecision(input.decision),
          edited_payload:
            input.editedPayload !== undefined
              ? { title: input.editedPayload.title, body: input.editedPayload.content }
              : null,
          idempotency_key: input.idempotencyKey,
        }),
      });
      if (!res.ok) throw toPlanningError(res.status, await res.json().catch(() => null));
      const envelope = (await res.json()) as DecisionEnvelope;
      return {
        approvalId: envelope.approvalId,
        decision: envelope.decision,
        version: envelope.version,
        originalPayload: envelope.originalPayload
          ? {
              title: envelope.originalPayload.title,
              content: envelope.originalPayload.body,
              category: envelope.originalPayload.type,
            }
          : undefined,
        document: envelope.document
          ? {
              id: envelope.document.id,
              title: envelope.document.title,
              content: envelope.document.body,
              category: envelope.document.type as Exclude<PlanningDocument["category"], "all">,
              version: envelope.document.version,
            }
          : undefined,
      };
    },

    async listVersions(documentId: string): Promise<DocumentVersion[]> {
      const res = await fetch(`${PLANNING_API}/documents/${documentId}/versions`);
      if (!res.ok) throw toPlanningError(res.status, await res.json().catch(() => null));
      const rows = (await res.json()) as VersionRow[];
      return rows.map((row) => ({
        id: row.id,
        documentId: row.documentId,
        version: row.version,
        title: row.title,
        content: row.body,
        createdAt: row.createdAt,
      }));
    },

    async restoreVersion(documentId: string, versionId: string): Promise<void> {
      const res = await fetch(
        `${PLANNING_API}/documents/${documentId}/versions/${versionId}/restore`,
        { method: "POST" },
      );
      if (!res.ok) throw toPlanningError(res.status, await res.json().catch(() => null));
    },

    async deleteDocument(documentId: string): Promise<void> {
      const res = await fetch(`${PLANNING_API}/documents/${documentId}`, { method: "DELETE" });
      if (!res.ok) throw toPlanningError(res.status, await res.json().catch(() => null));
    },

    async deleteVersion(documentId: string, versionId: string): Promise<void> {
      const res = await fetch(
        `${PLANNING_API}/documents/${documentId}/versions/${versionId}`,
        { method: "DELETE" },
      );
      if (!res.ok) throw toPlanningError(res.status, await res.json().catch(() => null));
    },

    async requestExecution(
      documentId: string,
      inputs: Record<string, unknown>,
      idempotencyKey: string,
    ): Promise<SkillExecution> {
      const res = await fetch(`${PLANNING_API}/skills/${documentId}/executions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ inputs, idempotency_key: idempotencyKey }),
      });
      if (!res.ok) throw toPlanningError(res.status, await res.json().catch(() => null));
      const envelope = (await res.json()) as ExecutionEnvelope;
      if (envelope.approval) {
        return { kind: "approval", approval: toExecutionApproval(envelope.approval) };
      }
      if (envelope.run) {
        return { kind: "run", run: toSandboxRun(envelope.run) };
      }
      throw new PlanningApiError("INTERNAL_ERROR", "Execution returned neither approval nor run.");
    },

    async decideExecutionApproval(
      approvalId: string,
      decision: ExecutionApprovalDecision,
      idempotencyKey: string,
    ): Promise<ExecutionDecisionResult> {
      const res = await fetch(`${PLANNING_API}/skills/approvals/${approvalId}/decisions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, idempotency_key: idempotencyKey }),
      });
      const envelope = (await res.json().catch(() => null)) as
        | (ExecutionDecisionEnvelope & {
            error?: { code?: string; message?: string; retryable?: boolean };
          })
        | null;
      if (!res.ok) {
        return {
          decision: "error",
          error: {
            code: envelope?.error?.code ?? "INTERNAL_ERROR",
            message: envelope?.error?.message ?? `Execution decision failed: ${res.status}`,
            ...(envelope?.error?.retryable !== undefined
              ? { retryable: envelope.error.retryable }
              : {}),
          },
        };
      }
      return {
        decision: envelope?.decision ?? "confirmed",
        ...(envelope?.run ? { run: toSandboxRun(envelope.run) } : {}),
      };
    },
  };
}
