# Second-Phase Todo List

**Status:** Not started

**Depends on:** The MVP backend architecture in [2026-08-05-mvp-backend-architecture-design.md](../superpowers/specs/2026-08-05-mvp-backend-architecture-design.md)

## 1. Administrator Control Plane

- [ ] Build role-protected administrator API and UI for account creation, disablement, and password reset.
- [ ] Add immutable audit views for administrative changes, without any route that can read or decrypt user message bodies.
- [ ] Provide management for memory, Skill, and MCP context-injection switches; record actor, prior value, new value, and timestamp.
- [ ] Verify that a disabled account cannot create a run, resume an Approval, or start a Sandbox execution.

**Acceptance:** Administrators can manage accounts and configuration, while API, database, logs, and UI tests prove that administrators cannot access ordinary users' chat or planning content.

## 2. Model Gateway and Cost Control

- [ ] Replace the single-provider `ModelGateway` implementation with LiteLLM Proxy behind the same interface.
- [ ] Add provider/model configuration, credentials stored by reference, context limits, defaults, enabled state, fallback rules, and model selection policy.
- [ ] Enforce global, user, provider, and model token/cost budgets before dispatching a model request.
- [ ] Store normalized usage, latency, provider status, and cost without message content.
- [ ] Add provider failover and a user-readable quota-exceeded response.

**Acceptance:** An administrator can change enabled models and budgets without a frontend or LangGraph contract change; disabled or over-budget routes cannot reach a provider.

## 3. Skill Lifecycle Management

- [ ] Add administrator and user views for Skill version comparison, activation, deactivation, deletion, run history, and rollback.
- [ ] Add explicit permission-review views that compare a pending Skill manifest with the active version.
- [ ] Add bounded scheduled or batch execution only after approval, queue capacity, retry, cancellation, and idempotency policies are specified.
- [ ] Add aggregate Skill failure and policy-denial reporting without exposing user content.

**Acceptance:** Every active Skill has a reviewable definition, a versioned permission history, a complete run history, and a reversible lifecycle transition.

## 4. MCP Registry Management

- [ ] Build an administrator UI for image-digest registration, provenance/SBOM metadata, isolated test results, network policy, and tool discovery.
- [ ] Require explicit server and tool enablement, tool effect classification, and a rollback path before an MCP is available to Skills.
- [ ] Add image signature verification and automated vulnerability-policy checks in the registration pipeline.
- [ ] Add health checks, quarantine state, compatibility reporting, and a release procedure for MCP image updates.

**Acceptance:** An administrator can safely register, validate, enable, disable, update, and roll back an MCP without allowing arbitrary runtime package installation or host command execution.

## 5. Sandbox Hardening and Operations

- [ ] Evaluate and adopt an OS-level stronger isolation runtime such as gVisor or a microVM for production Sandbox jobs.
- [ ] Route all permitted egress through a policy-aware proxy with DNS, redirect, and private-address controls.
- [ ] Add a secret broker for narrowly scoped, short-lived third-party credentials when a reviewed MCP requires them.
- [ ] Configure Celery worker autoscaling, task routing, dead-letter handling, retry policy, and capacity alarms.
- [ ] Run regular isolation, egress, resource-exhaustion, duplicate-delivery, and cancellation failure tests.

**Acceptance:** Production Sandbox runs have a documented isolation level, measurable resource limits, recoverable job handling, and tested containment of network and credential access.

## 6. Observability and Release Operations

- [ ] Integrate Langfuse and OpenTelemetry with field-level content redaction and request/run/sandbox correlation IDs.
- [ ] Add dashboards for request count, token/cost, latency, error rate, queue depth, Sandbox duration, policy denials, and MCP health.
- [ ] Add CI gates for migrations, RLS tests, OpenAPI/SSE compatibility, secret scanning, dependency/SBOM scanning, and Sandbox policy checks.
- [ ] Document production deployment, backup/restore, key rotation, incident response, rollback, and model-provider outage procedures.

**Acceptance:** Operations can identify a failed or costly run from safe metadata, recover from deployment or queue failures, and demonstrate that telemetry contains no user message or planning text.
