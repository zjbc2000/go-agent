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

// --- Chat runs (durable SSE run-first contract) ---

/** A stream of chat events plus the SSE Last-Event-ID cursor advanced as it is consumed. */
export interface RunStream {
  events: AsyncIterable<ChatEvent>;
  /** Current SSE cursor; undefined until the first frame with an id is read. */
  lastEventId: () => string | undefined;
}

/** A run created (or resumed) by POSTing to the run endpoint. */
export interface CreatedRun extends RunStream {
  runId: string;
}

/** Context needed to resume an interrupted run on the server. */
export interface RunStreamOptions {
  sessionId: string;
  content: string;
  idempotencyKey: string;
  lastEventId?: string;
  signal?: AbortSignal;
}

export type RunStatus = "streaming" | "completed" | "error";

/** A point-in-time reconstruction of a run from its persisted event replay. */
export interface RunSnapshot {
  runId: string;
  status: RunStatus;
  content: string;
}

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
  role: "user" | "admin";
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
