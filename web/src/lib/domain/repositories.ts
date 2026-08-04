// ============================================================
// Repository interfaces — page components depend on these,
// not on concrete implementations.
// ============================================================

import type {
  ApprovalDecision,
  ChatEvent,
  DocumentVersion,
  Message,
  PlanningDocument,
  PlanningFilter,
  PlanDraft,
  Session,
  UpdateDocumentInput,
  User,
} from "./types";

// --- Auth ---

export interface AuthRepository {
  login(email: string, password: string): Promise<User>;
  logout(): Promise<void>;
  getCurrentUser(): Promise<User | null>;
}

// --- Chat ---

export interface ChatRepository {
  listSessions(): Promise<Session[]>;
  getMessages(sessionId: string): Promise<Message[]>;
  sendMessage(
    sessionId: string,
    content: string,
    requestId: string,
  ): AsyncIterable<ChatEvent>;
  stopGeneration(messageId: string): Promise<void>;
  createSession(): Promise<Session>;
}

// --- Planning ---

export interface PlanningRepository {
  listDocuments(filter?: PlanningFilter): Promise<PlanningDocument[]>;
  updateDocument(
    id: string,
    input: UpdateDocumentInput,
  ): Promise<PlanningDocument>;
  decideApproval(id: string, decision: ApprovalDecision): Promise<void>;
  listVersions(documentId: string): Promise<DocumentVersion[]>;
  restoreVersion(documentId: string, versionId: string): Promise<void>;
}
