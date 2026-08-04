# MVP Backend Architecture Design

**Status:** Design approved; awaiting written-spec review

**Date:** 2026-08-05

## 1. Scope

This MVP delivers a complete, recoverable user workflow for:

- Supabase account-password authentication and browser session management.
- Session creation, message history, and SSE chat streaming.
- Durable Approval for document proposals and Skill execution.
- Versioned planning documents: `memory`, `interest`, `task`, and `skill`.
- A single LangGraph assistant backed by one OpenAI-compatible model provider.
- Declarative Skill execution, RabbitMQ/Celery scheduling, a sandbox, and controlled third-party MCP code.

The MVP does not include an administrator UI, LiteLLM, AutoGen teams, arbitrary user code, arbitrary host commands, public MCP installation, Elasticsearch, or scheduled reminders. The MVP has a controlled MCP registry; its initial administration happens through audited deployment configuration or an internal protected API. A full management console is a second-phase deliverable.

## 2. Architecture

```text
Browser
  -> Next.js BFF
      -> FastAPI agent-service
          -> Supabase Auth + PostgreSQL + RLS
          -> OpenAI-compatible model provider
          -> RabbitMQ -> Celery sandbox workers -> isolated Sandbox sidecars
                                              -> controlled MCP sidecars
```

### 2.1 Next.js BFF

The React/Next.js application is the only browser-facing service. It manages Supabase login, logout, token refresh, HttpOnly cookies, and the same-origin `/api/v1/*` API. It validates each user session before proxying an API or SSE request. It does not own business writes, agent orchestration, model credentials, or sandbox execution.

The BFF forwards the short-lived Supabase user JWT together with a short-lived internal request signature. The Agent service validates both. This prevents a browser from addressing the private Agent service directly while retaining the end-user identity needed for RLS.

### 2.2 FastAPI agent-service

`agent-service` is a FastAPI application. Its routers and application services cover request context, sessions/messages, runs/events, documents/versions, Approvals, Skills, tool brokering, and model access. Every request constructs an immutable `RequestContext` with `user_id`, `role`, JWT, and request ID; user identity is never read from process-global state.

The service is horizontally scalable because a durable `agent_run`, message state, and event log hold all recoverable state. It uses asynchronous I/O for model streams and never sends model SSE through Celery or RabbitMQ.

### 2.3 Persistence and identity

Supabase provides Auth and PostgreSQL. User-domain access is authenticated with the end-user JWT and constrained by PostgreSQL RLS. Migration and narrowly scoped operational jobs may use a service role; normal BFF, Agent, Sandbox, and MCP requests may not.

`agent-service` is the only service with access to the application encryption key in the cloud KMS. It decrypts message data only after user authorization. Sandbox workers, MCP sidecars, RabbitMQ, Celery task arguments, audit records, usage records, and operational logs never receive plaintext chat content.

### 2.4 Asynchronous execution

RabbitMQ and Celery are included only for Skill/Sandbox execution. A transaction creates `sandbox_run` and an `outbox_event`; an outbox publisher delivers the event to the `sandbox.execute` queue. The Celery task carries only `sandbox_run_id` and is idempotent. PostgreSQL remains the source of truth for the run status, audit trail, Approval relationship, and output summary.

This separates low-latency model streaming from durable, potentially slow or resource-constrained execution. Dedicated Sandbox workers can be scaled without scaling FastAPI API replicas.

## 3. Data Model

All user-domain tables contain `user_id`, and RLS restricts rows to the authenticated user. Administrative aggregate queries use purpose-built metadata views rather than user-content tables.

| Domain | Entities | Rules |
| --- | --- | --- |
| Identity and chat | `profile`, `session`, `message`, `agent_run`, `stream_event` | `(user_id, idempotency_key)` is unique for runs; `(run_id, sequence)` is unique for events. |
| Planning | `document`, `document_version`, `document_draft` | Formal documents only contain confirmed content. Drafts preserve the original model proposal and any user edit. Restoring a version creates a new version. |
| Approval and execution | `approval`, `sandbox_run`, `outbox_event` | Each Approval contains an immutable payload, hash, decision, actor, and expiry. Each Sandbox run pins one Skill document version and plan hash. |
| Tooling | `skill_definition`, `mcp_server`, `mcp_tool` | Skills bind a manifest to a document version. MCP servers and tools are disabled unless explicitly enabled. |

`messages` stores the final authoritative body as `content_ciphertext`. `stream_events` stores encrypted, time-bounded deltas while a run can reconnect; once a run reaches a terminal state, the full message body remains in `messages` and the replay events are removed after their retention period. Message metadata includes role, status, model reference, token usage, timestamps, and a safe error code, but not plaintext in observability records.

## 4. State Machines and Transactions

```text
agent_run: queued -> streaming -> waiting_approval -> completed | failed | cancelled
document_draft: pending -> approved | rejected | superseded
approval: pending -> approved | rejected | expired | cancelled
sandbox_run: queued -> running -> waiting_approval -> completed | failed | timed_out | cancelled
```

- Generating a planning proposal writes `document_draft` and `approval`; it never writes a formal document.
- Confirming a document proposal transactionally writes or updates `document`, appends `document_version`, resolves `approval`, and writes an audit record.
- A Skill invocation compiles the active `skill_definition` to an immutable execution plan. Write, delete, and external-effect steps create a new execution Approval. Its authorization is valid for exactly that plan for 15 minutes.
- Confirming an execution Approval transactionally resolves the Approval, creates `sandbox_run`, writes `outbox_event`, and emits the corresponding run event.
- A user action always includes an idempotency key. Duplicate submissions return the prior result rather than creating a duplicate document version, Approval, or Sandbox run.

## 5. Browser API and SSE Contract

The only browser-visible API prefix is `/api/v1`.

| Capability | Endpoints |
| --- | --- |
| Auth | `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` |
| Sessions | `POST /sessions`, `GET /sessions`, `GET /sessions/{id}/messages` |
| Runs | `POST /sessions/{id}/runs`, `GET /runs/{id}`, `GET /runs/{id}/events`, `POST /runs/{id}/cancel` |
| Documents | `GET/POST /documents`, `GET/PATCH/DELETE /documents/{id}`, `GET /documents/{id}/versions`, `POST /documents/{id}/versions/{versionId}/restore` |
| Approval | `GET /approvals?status=pending`, `POST /approvals/{id}/decisions` |
| Skill execution | `POST /skills/{documentId}/executions`, `GET /sandbox-runs/{id}`, `POST /sandbox-runs/{id}/cancel` |

`POST /sessions/{id}/runs` accepts an `Idempotency-Key` and returns `202` with `runId`, user message ID, assistant message ID, and status. The client then calls `GET /runs/{runId}/events`. SSE events have a continuous event ID and support `Last-Event-ID` replay:

```text
run.started
message.delta
document.draft
approval.required
skill.queued
skill.progress
skill.completed
run.completed
run.failed
```

Every event includes its event ID, run ID, timestamp, and a typed payload. A reconnect never re-submits the user message; it queries the run state and continues after the last event ID.

All non-streaming errors use this envelope:

```json
{
  "error": {
    "code": "APPROVAL_EXPIRED",
    "message": "The execution approval has expired.",
    "requestId": "...",
    "retryable": false
  }
}
```

Errors after an SSE response starts use `run.failed` with the same error object. The initial error-code catalog covers authentication, authorization, validation, idempotency conflicts, model availability/rate limits, interrupted streams, Approval conflicts/expiry, MCP availability, Sandbox policy denial, and Sandbox timeout.

## 6. Agent, Skills, MCP, and Sandbox

LangGraph implements one assistant only:

```text
load confirmed context -> model response/tool intent -> persist event
  -> create document draft OR compile Skill plan -> wait for Approval -> resume or complete
```

Only confirmed documents are loaded into assistant context. The model provider is accessed through a `ModelGateway` interface with one OpenAI-compatible implementation in production and a deterministic fake provider in tests.

Skills are declarative `manifest + steps` documents. Their definition includes a schema version, input schema, allowed platform tools, MCP tool references, runtime limits, and declared network needs. Skills cannot contain or execute arbitrary Python, JavaScript, Shell, package-install commands, or host file paths.

The MCP registry accepts remote HTTP MCPs and image-packaged `stdio` MCPs. An entry is accepted only after isolated validation and recording a fixed OCI image digest, provenance/SBOM, tool discovery result, network policy, and explicit server/tool allowlist. An arbitrary Git URL, package name, or Shell command is not an MCP registration format.

Each third-party MCP runs as a short-lived sidecar in an isolated Sandbox environment. Sandbox and sidecar environments run non-root with a read-only filesystem, no host mounts, no elevated capabilities, CPU/memory/disk/PID/runtime limits, and deny-by-default egress. Explicit egress checks resolve DNS and every redirect, rejecting loopback, private, link-local, and cloud-metadata addresses.

Sandbox code receives a one-time, short-lived token bound to one `sandbox_run`. It calls FastAPI's tool broker rather than the database or a provider directly. The broker revalidates the user, Skill version, current step, plan hash, Approval, idempotency key, tool allowlist, and network policy for every call.

## 7. Frontend Integration and Verification

The existing Repository abstraction remains. Production repositories use relative `/api/v1` paths; no public agent-service base URL is exposed. The real chat repository performs `createRun`, subscribes to events, and fetches run state when reconnecting. React pages and Zustand stores remain domain-oriented and do not parse raw HTTP/SSE details.

Mock repositories remain for component tests only. API integration and end-to-end tests use FastAPI, Supabase PostgreSQL/RLS, RabbitMQ/Celery, and deterministic fake provider/MCP/Sandbox implementations.

Required verification includes:

- Login, expired sessions, two-user RLS isolation, encrypted message-history retrieval, and no administrative message-body access.
- Idempotent run creation, incremental rendering, stream interruption, SSE replay, refresh recovery, cancellation, and provider failure.
- Draft editing, confirm/reject/regenerate, document-version restore, and Approval conflict/expiry.
- Skill execution Approval, duplicated Celery delivery, Sandbox timeout, policy denial, MCP unavailability, and MCP private-network blocking.
- Contract tests for OpenAPI, SSE schemas, and error codes; Playwright tests run through the BFF rather than a direct Agent endpoint.

Logs and telemetry record request/run IDs, status, latency, token/cost metadata, and safe error codes. They do not include message text, document text, decrypted event data, model prompts, MCP payloads, or secrets.

## 8. Deployment

- Vercel hosts Next.js and the BFF only.
- Supabase hosts Auth and PostgreSQL, including RLS-backed user data and encrypted content fields.
- A private container platform in the same region hosts FastAPI, RabbitMQ, Celery workers, Sandbox runtime, and MCP sidecars.
- The Agent service and BFF hold only the credentials appropriate to their role. Sandbox/MCP sidecars hold none of the database, provider, Supabase service-role, administrator, or KMS credentials.

## 9. Second Phase

The approved second-phase work is maintained in [todo-list.md](../../plan/todo-list.md). It adds an administrator control plane, LiteLLM model gateway, enhanced Skill/MCP lifecycle management, stronger Sandbox isolation, and redacted Langfuse/OpenTelemetry observability without changing the MVP user-facing contracts.

## 10. Reference Pattern

Hermes Agent is a reference for HTTP/SSE adapters, selective MCP tool loading, credential scoping, and OS-level process isolation. It is not embedded as this product's runtime because its documented trust model is single-tenant personal-agent first, while this product requires user-scoped RLS, immutable Approval, versioned planning data, and a controlled SaaS trust boundary.

References:

- https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/programmatic-integration.md
- https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/mcp.md
- https://github.com/NousResearch/hermes-agent/security
- https://docs.celeryq.dev/en/stable/userguide/concurrency/index.html
- https://www.postgresql.org/docs/current/sql-select.html
