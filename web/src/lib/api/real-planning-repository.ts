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
  PlanningDocument,
  PlanningFilter,
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

function toPlanningError(status: number, errBody: unknown): PlanningApiError {
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

    async updateDocument(): Promise<PlanningDocument> {
      throw new Error("updateDocument is not implemented; formal documents change via edit-confirm approvals");
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
  };
}
