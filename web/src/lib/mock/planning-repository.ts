// ============================================================
// Mock Planning Repository
// ============================================================

import type { PlanningRepository } from "@/lib/domain/repositories";
import type {
  ApprovalDecisionInput,
  ApprovalResult,
  DocumentVersion,
  PlanningDocument,
  PlanningFilter,
  UpdateDocumentInput,
} from "@/lib/domain/types";
import { generateId } from "@/lib/utils/id";

// --- In-memory store ---

let documents: PlanningDocument[] = [
  {
    id: "doc_1",
    title: "学习 Rust 编程",
    content: "每周学习 Rust 至少 5 小时，完成 Rustlings 全部练习，阅读《The Rust Book》前 10 章。",
    category: "interest",
    version: 3,
    createdAt: new Date(Date.now() - 7 * 86_400_000).toISOString(),
    updatedAt: new Date(Date.now() - 2 * 86_400_000).toISOString(),
    sourceSessionId: "sess_1",
  },
  {
    id: "doc_2",
    title: "周末晨跑计划",
    content: "每周六、周日上午 8:00 在朝阳公园晨跑 5 公里，配速控制在 6:00/km 以内。",
    category: "task",
    version: 2,
    createdAt: new Date(Date.now() - 5 * 86_400_000).toISOString(),
    updatedAt: new Date(Date.now() - 3 * 86_400_000).toISOString(),
  },
  {
    id: "doc_3",
    title: "记得给爸妈打电话",
    content: "每周至少一次视频通话，了解他们最近的身体和生活状况。",
    category: "memory",
    version: 1,
    createdAt: new Date(Date.now() - 10 * 86_400_000).toISOString(),
    updatedAt: new Date(Date.now() - 10 * 86_400_000).toISOString(),
  },
  {
    id: "doc_4",
    title: "摄影构图技巧",
    content: "练习三分法构图，每周出去街拍一次，整理作品集到 Lightroom。",
    category: "interest",
    version: 1,
    createdAt: new Date(Date.now() - 4 * 86_400_000).toISOString(),
    updatedAt: new Date(Date.now() - 4 * 86_400_000).toISOString(),
  },
  {
    id: "doc_5",
    title: "自动生成周报",
    content: "根据每日聊天记录，每周日晚自动汇总生成周报草稿，供用户确认。",
    category: "skill",
    version: 2,
    createdAt: new Date(Date.now() - 14 * 86_400_000).toISOString(),
    updatedAt: new Date(Date.now() - 6 * 86_400_000).toISOString(),
  },
];

const versions: Record<string, DocumentVersion[]> = {
  doc_1: [
    {
      id: "ver_1",
      documentId: "doc_1",
      version: 1,
      title: "学习 Rust",
      content: "学习 Rust 编程语言。",
      createdAt: new Date(Date.now() - 7 * 86_400_000).toISOString(),
    },
    {
      id: "ver_2",
      documentId: "doc_1",
      version: 2,
      title: "学习 Rust 编程",
      content: "每周学习 Rust 至少 3 小时，完成 Rustlings 基础练习。",
      createdAt: new Date(Date.now() - 4 * 86_400_000).toISOString(),
    },
    {
      id: "ver_3",
      documentId: "doc_1",
      version: 3,
      title: "学习 Rust 编程",
      content: "每周学习 Rust 至少 5 小时，完成 Rustlings 全部练习，阅读《The Rust Book》前 10 章。",
      createdAt: new Date(Date.now() - 2 * 86_400_000).toISOString(),
    },
  ],
};

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

// --- Implementation ---

export function createMockPlanningRepository(): PlanningRepository {
  return {
    async listDocuments(filter?: PlanningFilter): Promise<PlanningDocument[]> {
      await delay(200);
      let result = [...documents];

      if (filter?.category && filter.category !== "all") {
        result = result.filter((d) => d.category === filter.category);
      }

      if (filter?.search) {
        const q = filter.search.toLowerCase();
        result = result.filter(
          (d) =>
            d.title.toLowerCase().includes(q) ||
            d.content.toLowerCase().includes(q),
        );
      }

      return result;
    },

    async updateDocument(
      id: string,
      input: UpdateDocumentInput,
    ): Promise<PlanningDocument> {
      await delay(150);
      const doc = documents.find((d) => d.id === id);
      if (!doc) throw new Error(`Document ${id} not found`);

      const newVersion = doc.version + 1;
      const updated: PlanningDocument = {
        ...doc,
        ...input,
        version: newVersion,
        updatedAt: new Date().toISOString(),
      };

      // Save version history
      const versionEntry: DocumentVersion = {
        id: `ver_${generateId("v")}`,
        documentId: id,
        version: doc.version,
        title: doc.title,
        content: doc.content,
        createdAt: doc.updatedAt,
      };

      versions[id] = [...(versions[id] ?? []), versionEntry];
      documents = documents.map((d) => (d.id === id ? updated : d));

      return updated;
    },

    async decideApproval(input: ApprovalDecisionInput): Promise<ApprovalResult> {
      await delay(100);
      if (input.decision === "confirm") {
        const title = input.editedPayload?.title ?? "新规划文档";
        const content = input.editedPayload?.content ?? "新规划文档内容";
        const document: PlanningDocument = {
          id: `doc_${generateId("d")}`,
          title,
          content,
          category: "task",
          version: 1,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        };
        documents = [document, ...documents];
        return {
          approvalId: input.approvalId,
          decision: "confirmed",
          version: 1,
          originalPayload: { title, content, category: "task" },
          document: {
            id: document.id,
            title,
            content,
            category: "task",
            version: 1,
          },
        };
      }
      if (input.decision === "reject") {
        return { approvalId: input.approvalId, decision: "rejected", version: null };
      }
      // Regenerate: the proposal is sent back; the list is left unchanged.
      return { approvalId: input.approvalId, decision: "superseded", version: null };
    },

    async listVersions(documentId: string): Promise<DocumentVersion[]> {
      await delay(100);
      return [...(versions[documentId] ?? [])];
    },

    async restoreVersion(
      documentId: string,
      versionId: string,
    ): Promise<void> {
      await delay(150);
      const docVersions = versions[documentId];
      if (!docVersions) throw new Error("No versions found");

      const target = docVersions.find((v) => v.id === versionId);
      if (!target) throw new Error(`Version ${versionId} not found`);

      documents = documents.map((d) =>
        d.id === documentId
          ? {
              ...d,
              title: target.title,
              content: target.content,
              version: d.version + 1,
              updatedAt: new Date().toISOString(),
            }
          : d,
      );
    },
  };
}
