// ============================================================
// Repository interfaces — page components depend on these,
// not on concrete implementations.
// ============================================================

import type {
  ApprovalDecision,
  CreatedRun,
  DocumentVersion,
  Message,
  PlanningDocument,
  PlanningFilter,
  PlanDraft,
  RunSnapshot,
  RunStream,
  RunStreamOptions,
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

  /**
   * Create (or resume) a run and stream its events.
   *
   * POSTs the run with the given idempotency key, reads the `run.started` frame to
   * learn the durable `runId`, and exposes the live stream from that point onward.
   */
  createRun(
    sessionId: string,
    content: string,
    idempotencyKey: string,
    options?: { signal?: AbortSignal },
  ): Promise<CreatedRun>;

  /**
   * Resume an interrupted run. Re-POSTs the SAME session + idempotency key with a
   * `Last-Event-ID` cursor, so the server resumes the existing run's live generation
   * without creating a second user message.
   */
  subscribeRunEvents(options: RunStreamOptions): RunStream;

  /** Reconstruct a run's current status and text from its persisted event replay. */
  getRun(runId: string): Promise<RunSnapshot | null>;

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
