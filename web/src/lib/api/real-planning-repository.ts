// ============================================================
// Real Planning Repository — placeholder, replace when API is ready
// ============================================================

import type { PlanningRepository } from "@/lib/domain/repositories";
import type {
  ApprovalDecision,
  DocumentVersion,
  PlanningDocument,
  PlanningFilter,
  UpdateDocumentInput,
} from "@/lib/domain/types";

export function createRealPlanningRepository(): PlanningRepository {
  const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

  return {
    async listDocuments(filter?: PlanningFilter): Promise<PlanningDocument[]> {
      const params = new URLSearchParams();
      if (filter?.category) params.set("category", filter.category);
      if (filter?.search) params.set("search", filter.search);
      const res = await fetch(`${API_BASE}/api/planning?${params}`);
      if (!res.ok) throw new Error("Failed to list documents");
      return res.json();
    },

    async updateDocument(id: string, input: UpdateDocumentInput): Promise<PlanningDocument> {
      const res = await fetch(`${API_BASE}/api/planning/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      });
      if (!res.ok) throw new Error("Failed to update document");
      return res.json();
    },

    async decideApproval(id: string, decision: ApprovalDecision): Promise<void> {
      const res = await fetch(`${API_BASE}/api/planning/${id}/approval`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision }),
      });
      if (!res.ok) throw new Error("Failed to decide approval");
    },

    async listVersions(documentId: string): Promise<DocumentVersion[]> {
      const res = await fetch(`${API_BASE}/api/planning/${documentId}/versions`);
      if (!res.ok) throw new Error("Failed to list versions");
      return res.json();
    },

    async restoreVersion(documentId: string, versionId: string): Promise<void> {
      const res = await fetch(
        `${API_BASE}/api/planning/${documentId}/versions/${versionId}/restore`,
        { method: "POST" },
      );
      if (!res.ok) throw new Error("Failed to restore version");
    },
  };
}
