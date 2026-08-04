# MVP Declarative Skills, Sandbox, and MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute approved, versioned declarative Skills through a RabbitMQ/Celery-scheduled isolated Sandbox and controlled third-party MCP sidecars.

**Architecture:** FastAPI compiles a pinned Skill document version into an immutable execution plan, creates execution Approval when a step can write/delete/create an external side effect, and publishes `sandbox_run_id` through a transactional outbox. Celery runs a short-lived isolated sandbox whose only data access is a one-time FastAPI tool-broker token; controlled MCP images run as sidecars with server/tool allowlists.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy 2, Supabase PostgreSQL/RLS, RabbitMQ, Celery, Docker or OCI runtime, Python pytest, Testcontainers, Next.js, Vitest, Playwright.

## Global Constraints

- Execute after the Foundation/Chat and Documents/Approvals plans pass their tests.
- A Skill is `manifest + steps`, never arbitrary Python, JavaScript, Shell, package installation, Git URL, or host file path.
- RabbitMQ/Celery handles only sandbox work; model SSE stays in FastAPI.
- Every write/delete/external-effect step requires a plan-specific Approval that expires after 15 minutes.
- The Celery payload contains only `sandbox_run_id`; all side effects are idempotent by `(sandbox_run_id, step_id)`.
- Sandbox and MCP sidecars run non-root, with read-only filesystems, no host mounts/capabilities, resource limits, no DB/provider/KMS/admin credentials, and deny-by-default egress.
- MCP admission requires a fixed OCI image digest, provenance/SBOM, isolated validation, tool discovery, and explicit server/tool enablement.

---

## File Structure

- Create: `infra/compose/dev-compose.yml` - Agent, RabbitMQ, Celery, sandbox runtime, Supabase local service wiring.
- Create: `agent-service/app/skills/{schemas,compiler,service,router}.py` - manifest validation, immutable planning, API routes.
- Create: `agent-service/app/execution/{outbox,publisher,tool_broker}.py` - transactional delivery and policy-rechecking tool proxy.
- Create: `supabase/migrations/202608050004_execution.sql` - Skill definitions, sandbox runs, outbox, MCP registry, RLS/indexes.
- Create: `sandbox/app/{celery_app,tasks,runtime,policy,tool_client}.py` - queue consumer, isolated runtime launcher, resource/network policy, tool calls.
- Create: `sandbox/tests/{conftest.py,test_task_idempotency.py,test_policy.py,test_runtime.py,test_mcp_client.py}` and `agent-service/tests/execution/` - plan, approval, outbox, duplicate-delivery, token, and policy tests.
- Create: `mcp-registry/example-readonly/{Dockerfile,server.py,manifest.json}` - fixed-digest test MCP image source.
- Modify: `web/src/lib/domain/{types,repositories}.ts`, `web/src/lib/api/real-planning-repository.ts`, `web/src/components/approval/PlanDraftCard.tsx` - Skill/execution Approval rendering.
- Create: `web/e2e/skill-execution.spec.ts` - real BFF/API Skill workflow.
- Create: `web/e2e/support/skill.ts` - seeded Skill execution and expiry helpers.

## Task 1: Skill Definitions, Immutable Plans, and Execution Approvals

**Files:**
- Create: `supabase/migrations/202608050004_execution.sql`, `agent-service/app/skills/schemas.py`, `agent-service/app/skills/compiler.py`, `agent-service/app/skills/service.py`, `agent-service/app/skills/router.py`
- Test: `agent-service/tests/execution/test_compiler.py`, `agent-service/tests/execution/test_execution_approval.py`

**Interfaces:**
- Consumes: confirmed `document_version`, `Approval`, and `RequestContext`.
- Produces: `compile_skill(version, inputs) -> ExecutionPlan`, `request_execution(context, document_id, inputs, idempotency_key) -> Approval | SandboxRun`, `POST /internal/v1/skills/{document_id}/executions`.

- [ ] **Step 1: Write failing manifest and approval tests**

```python
def test_compiler_rejects_unregistered_tool():
    manifest = {"schema_version": 1, "steps": [{"id": "s1", "tool": "shell.exec", "input": {}}]}
    with pytest.raises(ApiError, match="SKILL_INVALID"):
        compile_skill(manifest, {})

async def test_write_step_creates_expiring_approval(service, user_context, active_skill):
    result = await service.request_execution(user_context, active_skill, {"title": "x"}, "run-1")
    assert result.approval.expires_at - result.approval.created_at == timedelta(minutes=15)
```

- [ ] **Step 2: Run compiler and Approval tests to verify they fail**

Run: `cd agent-service && uv run pytest tests/execution/test_compiler.py tests/execution/test_execution_approval.py -v`

Expected: FAIL because the Skill schema, registry, and execution service are absent.

- [ ] **Step 3: Implement manifest validation and immutable plan compilation**

Accept only `document.read`, `document.create`, `document.update`, `document.delete`, and registered MCP tool IDs. Normalize inputs, substitute them into a copied plan, validate all step schemas, and compute `sha256(canonical_json(plan))`. Bind `skill_definition` to the exact `document_version`; a changed Skill requires a new version and plan.

```python
def compile_skill(definition: SkillDefinition, inputs: dict[str, Any]) -> ExecutionPlan:
    steps = [compile_step(step, inputs, definition.allowed_tools) for step in definition.manifest.steps]
    payload = {"skill_version_id": str(definition.version_id), "steps": [step.model_dump(mode="json") for step in steps]}
    return ExecutionPlan.model_validate({**payload, "hash": sha256(canonical_json(payload)).hexdigest()})
```

- [ ] **Step 4: Run focused execution tests**

Run: `supabase db reset && cd agent-service && uv run pytest tests/execution/test_compiler.py tests/execution/test_execution_approval.py -v`

Expected: PASS; read-only plans can queue directly and write/delete plans wait for an unexpired immutable Approval.

- [ ] **Step 5: Commit Skill planning**

```bash
git add supabase/migrations/202608050004_execution.sql agent-service/app/skills agent-service/tests/execution
git commit -m "feat(skill): add declarative plans and execution approvals"
```

## Task 2: Transactional Outbox, RabbitMQ, and Celery Scheduling

**Files:**
- Create: `agent-service/app/execution/outbox.py`, `agent-service/app/execution/publisher.py`, `sandbox/app/celery_app.py`, `sandbox/app/tasks.py`, `infra/compose/dev-compose.yml`
- Test: `agent-service/tests/execution/test_outbox.py`, `sandbox/tests/test_task_idempotency.py`

**Interfaces:**
- Consumes: approved `ExecutionPlan` and `sandbox_run` transaction.
- Produces: `enqueue_sandbox_run(...) -> SandboxRun`, `publish_pending_outbox_events() -> int`, Celery task `execute_sandbox_run(sandbox_run_id: str) -> None`.

- [ ] **Step 1: Write failing outbox and duplicate-delivery tests**

```python
async def test_confirming_execution_creates_unpublished_outbox_row(service, user_context, approval):
    run = await service.confirm_execution(user_context, approval.id, "confirm-1")
    assert await outbox.has_event("sandbox.execute", run.id)

def test_duplicate_task_executes_each_step_once(worker, seeded_run):
    worker.execute_sandbox_run(seeded_run.id)
    worker.execute_sandbox_run(seeded_run.id)
    assert audit.count(step_id="write-title") == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd agent-service && uv run pytest tests/execution/test_outbox.py -v`

Run: `cd sandbox && uv run pytest tests/test_task_idempotency.py -v`

Expected: FAIL because no durable publisher or worker exists.

- [ ] **Step 3: Implement the outbox publisher and Celery worker boundary**

Insert `sandbox_runs` and `outbox_events` in the same commit that approves an execution. Publish with RabbitMQ confirms, mark the event published only after broker acknowledgement, and retry unpublished events with bounded backoff. Configure a dedicated `sandbox.execute` queue and use `sandbox_run_id` as the Celery task argument and idempotency lookup key.

```python
@celery_app.task(name="sandbox.execute", acks_late=True)
def execute_sandbox_run(sandbox_run_id: str) -> None:
    if repository.is_terminal(sandbox_run_id):
        return
    runtime.execute(repository.claim(sandbox_run_id))
```

- [ ] **Step 4: Run integration tests with RabbitMQ**

Run: `docker compose -f infra/compose/dev-compose.yml up -d rabbitmq`

Run: `cd agent-service && uv run pytest tests/execution/test_outbox.py -v && cd ../sandbox && uv run pytest tests/test_task_idempotency.py -v`

Expected: PASS; a worker crash or duplicate delivery cannot create duplicate tool writes.

- [ ] **Step 5: Commit reliable sandbox scheduling**

```bash
git add agent-service/app/execution sandbox/app infra/compose/dev-compose.yml agent-service/tests/execution sandbox/tests
git commit -m "feat(execution): schedule sandbox runs through outbox and Celery"
```

## Task 3: Isolated Sandbox Runtime and FastAPI Tool Broker

**Files:**
- Create: `sandbox/app/runtime.py`, `sandbox/app/policy.py`, `sandbox/app/tool_client.py`, `agent-service/app/execution/tool_broker.py`
- Test: `sandbox/tests/test_policy.py`, `sandbox/tests/test_runtime.py`, `agent-service/tests/execution/test_tool_broker.py`

**Interfaces:**
- Produces: `SandboxPolicy.validate_url(url) -> None`, `Runtime.execute(plan, grant) -> ExecutionResult`, `ToolBroker.invoke(grant, step_id, tool_id, input) -> ToolResult`.

- [ ] **Step 1: Write failing isolation and broker tests**

```python
@pytest.mark.parametrize("url", ["http://127.0.0.1", "http://169.254.169.254", "http://10.0.0.1"])
def test_network_policy_rejects_non_public_targets(url):
    with pytest.raises(PolicyDenied):
        SandboxPolicy().validate_url(url)

async def test_broker_rejects_expired_or_wrong_step_grant(broker, expired_grant):
    with pytest.raises(ApiError, match="SANDBOX_GRANT_INVALID"):
        await broker.invoke(expired_grant, "other-step", "document.update", {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sandbox && uv run pytest tests/test_policy.py tests/test_runtime.py -v`

Run: `cd agent-service && uv run pytest tests/execution/test_tool_broker.py -v`

Expected: FAIL because the runtime, grant verifier, and network policy are absent.

- [ ] **Step 3: Implement per-run isolation and mediated tools**

Launch each run with a non-root OCI runtime configuration: read-only root, empty writable temp mount with size cap, no host mounts, dropped capabilities, PID/memory/CPU/time limits, and deny-by-default egress. Issue a one-time signed grant with run ID, user ID, plan hash, expiry, and allowed step IDs. The broker reloads the run and Approval from PostgreSQL before each tool call, validates the grant, and records a redacted audit event.

```python
def validate_url(url: str) -> None:
    for address in resolve_all(url):
        if not address.is_global:
            raise PolicyDenied("SANDBOX_NETWORK_DENIED", "Target address is not public.")
```

- [ ] **Step 4: Run sandbox policy, runtime, and broker tests**

Run: `cd sandbox && uv run pytest tests/test_policy.py tests/test_runtime.py -v && cd ../agent-service && uv run pytest tests/execution/test_tool_broker.py -v`

Expected: PASS; no sandbox process receives application secrets and invalid grants cause no data mutation.

- [ ] **Step 5: Commit sandbox containment**

```bash
git add sandbox/app sandbox/tests agent-service/app/execution/tool_broker.py agent-service/tests/execution
git commit -m "feat(sandbox): isolate declarative runs and broker tools"
```

## Task 4: Controlled Third-Party MCP Sidecars

**Files:**
- Create: `mcp-registry/example-readonly/Dockerfile`, `mcp-registry/example-readonly/server.py`, `mcp-registry/example-readonly/manifest.json`
- Create: `agent-service/app/mcp/{schemas,registry,validator}.py`, `sandbox/app/mcp_client.py`
- Test: `agent-service/tests/execution/test_mcp_registry.py`, `sandbox/tests/test_mcp_client.py`

**Interfaces:**
- Produces: `register_mcp(manifest, image_digest) -> McpServer`, `discover_tools(server_id) -> list[McpTool]`, `McpClient.invoke(server, tool, input, grant) -> ToolResult`.

- [ ] **Step 1: Write failing MCP admission and allowlist tests**

```python
def test_registry_rejects_mutable_image_tag(registry):
    with pytest.raises(ApiError, match="MCP_DIGEST_REQUIRED"):
        registry.register({"image": "registry.example/tool:latest"})

async def test_disabled_tool_cannot_be_invoked(client, registered_server, grant):
    with pytest.raises(ApiError, match="MCP_TOOL_DISABLED"):
        await client.invoke(registered_server, "delete_everything", {}, grant)
```

- [ ] **Step 2: Run MCP tests to verify they fail**

Run: `cd agent-service && uv run pytest tests/execution/test_mcp_registry.py -v && cd ../sandbox && uv run pytest tests/test_mcp_client.py -v`

Expected: FAIL because no immutable-image validation or sidecar client exists.

- [ ] **Step 3: Implement registry validation and sidecar invocation**

Require `image@sha256:<digest>`, provenance/SBOM metadata, an isolated discovery result, network policy, and explicit enabled tools before marking a server enabled. Start the MCP image only as a Sandbox sidecar under the same runtime policy. Connect through a local sandbox-only transport; pass no host credentials or database connection. Treat every MCP result as untrusted text and validate it against the registered tool output schema.

```python
if not manifest.image.startswith("oci://") or "@sha256:" not in manifest.image:
    raise ApiError("MCP_DIGEST_REQUIRED", "MCP image must be pinned by digest.", False)
if tool_id not in server.enabled_tool_ids:
    raise ApiError("MCP_TOOL_DISABLED", "MCP tool is disabled.", False)
```

- [ ] **Step 4: Run MCP registry and sidecar tests**

Run: `cd agent-service && uv run pytest tests/execution/test_mcp_registry.py -v && cd ../sandbox && uv run pytest tests/test_mcp_client.py -v`

Expected: PASS; mutable images and disabled tools are rejected before a sidecar starts.

- [ ] **Step 5: Commit controlled MCP execution**

```bash
git add mcp-registry agent-service/app/mcp sandbox/app/mcp_client.py agent-service/tests/execution sandbox/tests
git commit -m "feat(mcp): add verified sidecar registry and allowlists"
```

## Task 5: Skill Execution UI, Errors, and End-to-End Safety Tests

**Files:**
- Modify: `web/src/lib/domain/types.ts`, `web/src/lib/domain/repositories.ts`, `web/src/lib/api/real-planning-repository.ts`, `web/src/components/approval/PlanDraftCard.tsx`
- Create: `web/src/components/approval/SkillExecutionCard.tsx`, `web/e2e/skill-execution.spec.ts`
- Create: `web/e2e/support/skill.ts`

**Interfaces:**
- Consumes: execution/Approval APIs from Tasks 1-4.
- Produces: cards that display plan summary, status, expiry, policy denial, timeout, and retryable/non-retryable state without displaying secrets or raw MCP payloads.

- [ ] **Step 1: Write failing UI and real-stack E2E tests**

```ts
test("a document write waits for approval and an expired decision does not run", async ({ page }) => {
  await loginAsSeededUser(page);
  await startSeededWriteSkill(page);
  await expect(page.getByText("等待确认")).toBeVisible();
  await expireExecutionApproval();
  await page.getByRole("button", { name: "确认执行" }).click();
  await expect(page.getByText("该执行确认已过期")).toBeVisible();
});
```

```ts
export async function startSeededWriteSkill(page: Page) { await page.getByRole("button", { name: "执行 Skill" }).click(); }
export async function expireExecutionApproval() { await fetch(`${process.env.TEST_API_BASE}/test/expire-latest-approval`, { method: "POST" }); }
```

- [ ] **Step 2: Run the UI and E2E tests to verify they fail**

Run: `cd web && pnpm test:e2e -- skill-execution.spec.ts`

Expected: FAIL because execution cards and real sandbox statuses are absent.

- [ ] **Step 3: Implement execution cards and error-state mapping**

Render the immutable plan summary and expiry before confirmation. Map `SANDBOX_POLICY_DENIED`, `SANDBOX_TIMEOUT`, `MCP_UNAVAILABLE`, and `APPROVAL_EXPIRED` to distinct accessible UI text; retain a retry action only for errors marked `retryable: true` by the API.

```tsx
{run.error?.code === "SANDBOX_TIMEOUT" ? <p role="alert">执行超时，未完成的步骤未被提交。</p> : null}
```

- [ ] **Step 4: Run complete execution verification**

Run: `cd agent-service && uv run pytest tests/execution -v && cd ../sandbox && uv run pytest tests -v && cd ../web && pnpm test && pnpm test:e2e -- skill-execution.spec.ts`

Expected: PASS; write/delete steps cannot occur before an unexpired matching Approval and an MCP blocked by policy cannot reach the network.

- [ ] **Step 5: Commit Skill UI and end-to-end tests**

```bash
git add web/src/lib/domain web/src/lib/api/real-planning-repository.ts web/src/components/approval web/e2e/skill-execution.spec.ts
git commit -m "feat(web): show real skill execution approvals and states"
```
