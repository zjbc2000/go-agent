// ============================================================
// Domain types — single source of truth for all frontend types
// ============================================================

// --- Chat ---

export type MessageRole = "user" | "assistant";

export type MessageStatus = "sending" | "sent" | "streaming" | "completed" | "error";

export interface Message {
  id: string;
  sessionId: string;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  createdAt: string;
  error?: string;
}

export interface Session {
  id: string;
  title: string;
  lastMessageAt: string;
  createdAt: string;
}

// --- Chat Events (SSE stream) ---

export type ChatEvent =
  | { type: "message-start"; messageId: string }
  | { type: "token"; messageId: string; text: string }
  | { type: "draft"; draft: PlanDraft }
  | { type: "done"; messageId: string }
  | { type: "error"; code: string; message: string };

// --- Planning ---

export type PlanningCategory = "all" | "memory" | "interest" | "task" | "skill";

export interface PlanDraft {
  id: string;
  sessionId: string;
  messageId: string;
  title: string;
  content: string;
  category: PlanningCategory;
  status: PlanDraftStatus;
  createdAt: string;
}

export type PlanDraftStatus =
  | "pending_confirmation"
  | "editing"
  | "saving"
  | "confirmed"
  | "rejected"
  | "regenerating";

export interface PlanningDocument {
  id: string;
  title: string;
  content: string;
  category: Exclude<PlanningCategory, "all">;
  version: number;
  createdAt: string;
  updatedAt: string;
  sourceSessionId?: string;
}

export interface DocumentVersion {
  id: string;
  documentId: string;
  version: number;
  title: string;
  content: string;
  createdAt: string;
}

export type ApprovalDecision = "confirm" | "reject" | "regenerate";

export interface UpdateDocumentInput {
  title?: string;
  content?: string;
  category?: Exclude<PlanningCategory, "all">;
}

export interface PlanningFilter {
  category?: PlanningCategory;
  search?: string;
}

// --- Auth ---

export interface User {
  id: string;
  name: string;
  email: string;
  avatarUrl?: string;
}

// --- Chat state machine ---

export type ChatState =
  | "idle"
  | "connecting"
  | "streaming"
  | "stopped"
  | "reconnecting"
  | "completed"
  | "error";
