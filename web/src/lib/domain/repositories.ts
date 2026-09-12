// ============================================================
// Repository interfaces — page components depend on these,
// not on concrete implementations.
// ============================================================

import type {
  ApprovalDecisionInput,
  ApprovalResult,
  CreateEmployeeInput,
  CreatedRun,
  DocumentVersion,
  Employee,
  EmployeeActionApproval,
  EmployeeActionKind,
  EmployeeDecisionResult,
  ExecutionApprovalDecision,
  ExecutionDecisionResult,
  Message,
  PlanningDocument,
  PlanningFilter,
  RunSnapshot,
  RunStream,
  RunStreamOptions,
  Session,
  SkillExecution,
  UpdateDocumentInput,
  UpdateEmployeeInput,
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

  /** Create a session, optionally named and optionally bound to an employee. */
  createSession(title?: string, employeeId?: string): Promise<Session>;
  deleteSession(sessionId: string): Promise<void>;
  renameSession(sessionId: string, title: string): Promise<void>;
}

// --- Planning ---

export interface PlanningRepository {
  listDocuments(filter?: PlanningFilter): Promise<PlanningDocument[]>;
  updateDocument(
    id: string,
    input: UpdateDocumentInput,
  ): Promise<PlanningDocument>;
  decideApproval(input: ApprovalDecisionInput): Promise<ApprovalResult>;
  listVersions(documentId: string): Promise<DocumentVersion[]>;
  restoreVersion(documentId: string, versionId: string): Promise<void>;
  deleteDocument(documentId: string): Promise<void>;
  deleteVersion(documentId: string, versionId: string): Promise<void>;

  /**
   * Request a skill execution. Write/delete skills return a pending execution
   * approval; read-only skills return a queued run. Card state is client-side
   * from this response (a GET executions route is future work).
   */
  requestExecution(
    documentId: string,
    inputs: Record<string, unknown>,
    idempotencyKey: string,
  ): Promise<SkillExecution>;

  /** Decide a pending execution approval (the UI sends "approve" for 确认执行). */
  decideExecutionApproval(
    approvalId: string,
    decision: ExecutionApprovalDecision,
    idempotencyKey: string,
  ): Promise<ExecutionDecisionResult>;
}

// --- Company / employees ---

export interface CompanyRepository {
  listEmployees(): Promise<Employee[]>;
  createEmployee(input: CreateEmployeeInput): Promise<Employee>;
  updateEmployee(id: string, input: UpdateEmployeeInput): Promise<Employee>;

  /**
   * Open a HITL approval for a fire/rehire/adjust_position action. The pending
   * approval is returned; the transition applies only when the owner decides it.
   */
  requestEmployeeAction(input: {
    employeeId: string;
    action: EmployeeActionKind;
    position?: string;
    idempotencyKey: string;
  }): Promise<EmployeeActionApproval>;

  /** Decide a pending employee action ("approve" applies the transition). */
  decideEmployeeApproval(
    approvalId: string,
    decision: "approve" | "reject",
    idempotencyKey: string,
  ): Promise<EmployeeDecisionResult>;
}
