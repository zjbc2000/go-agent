# MVP Documents, Approvals, and Web Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add user-scoped versioned planning documents and immutable Approval workflows, then connect the existing planning and draft UI to real BFF APIs.

**Architecture:** FastAPI owns drafts, formal documents, versions, Approval state, and audit transactions. The frontend continues to depend on `PlanningRepository`, with cards and lists consuming real status data rather than mutating local mock state.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy 2, Supabase PostgreSQL/RLS, KMS encryption, Next.js 16.3, TypeScript, Zustand, Vitest, Playwright.

## Global Constraints

- Execute after `2026-08-05-mvp-foundation-and-chat.md` has passed its tests.
- Planning categories are exactly `memory`, `interest`, `task`, and `skill`.
- A pending draft is not a formal document and cannot be injected into model context.
- A confirm, edit-confirm, reject, regenerate, restore, or delete action is idempotent and auditable.
- Formal planning text and draft payloads use the same application encryption policy as messages.
- Administrators cannot query or decrypt planning content through this MVP API.

---

## File Structure

- Create: `supabase/migrations/202608050003_planning.sql` - documents, versions, drafts, approvals, audit records, RLS, indexes.
- Create: `agent-service/app/models/planning.py`, `agent-service/app/repositories/planning.py` - encrypted domain persistence.
- Create: `agent-service/app/planning/{schemas,service,router}.py` - document/version routes and transactional Approval decisions.
- Modify: `agent-service/app/chat/service.py` - persist model-produced document drafts and emit `document.draft`/`approval.required`.
- Create: `agent-service/tests/planning/` - repository, API, versioning, RLS, and conflict tests.
- Modify: `web/src/lib/domain/{types,repositories}.ts`, `web/src/lib/api/real-planning-repository.ts` - explicit Approval and version responses.
- Modify: `web/src/components/approval/{PlanDraftCard,ApprovalActions,InlinePlanEditor}.tsx`, `web/src/components/planning/{PlanningList,PlanningDocument,VersionHistory}.tsx` - server-driven decision/error states.
- Create: `web/src/lib/api/__tests__/real-planning-repository.test.ts`, `web/e2e/approval-and-versions.spec.ts`.
- Create: `web/e2e/support/planning.ts` - seeded-user, draft-confirm, and restore workflow helpers.

## Task 1: Versioned Planning Schema and RLS

**Files:**
- Create: `supabase/migrations/202608050003_planning.sql`, `agent-service/app/models/planning.py`, `agent-service/app/repositories/planning.py`
- Test: `agent-service/tests/planning/test_repository.py`, `agent-service/tests/planning/test_rls.py`

**Interfaces:**
- Produces: `DocumentType`, `DocumentStatus`, `DocumentRepository.list_active(context, filter)`, `DocumentRepository.restore_version(context, document_id, version_id)`.

- [ ] **Step 1: Write failing version and RLS tests**

```python
async def test_restoring_a_version_creates_a_new_current_version(repository, user_context):
    document = await repository.create_active(user_context, type="task", title="v1", body="first")
    updated = await repository.update_active(user_context, document.id, title="v2", body="second")
    restored = await repository.restore_version(user_context, document.id, document.current_version_id)
    assert restored.version == updated.version + 1

async def test_user_cannot_read_another_users_document(repository, user_a, user_b):
    document = await repository.create_active(user_a, type="memory", title="private", body="secret")
    assert await repository.get(user_b, document.id) is None
```

- [ ] **Step 2: Run the schema tests to verify they fail**

Run: `cd agent-service && uv run pytest tests/planning/test_repository.py tests/planning/test_rls.py -v`

Expected: FAIL because planning tables and repositories are absent.

- [ ] **Step 3: Create formal-document and draft tables**

Create `documents`, `document_versions`, `document_drafts`, `approvals`, and `audit_logs`. Put `user_id` on every table, encrypt content fields, and create RLS policies by `auth.uid()`. Use a unique `(document_id, version)` constraint; create a new version row for every active-document write and restore.

```sql
create table public.documents (
  id uuid primary key default gen_random_uuid(), user_id uuid not null references auth.users(id),
  type text not null check (type in ('memory','interest','task','skill')),
  current_version integer not null default 1, status text not null check (status = 'active')
);
create unique index documents_version_idx on public.document_versions(document_id, version);
```

- [ ] **Step 4: Reset the database and run repository/RLS tests**

Run: `supabase db reset && cd agent-service && uv run pytest tests/planning/test_repository.py tests/planning/test_rls.py -v`

Expected: PASS; a restore appends a version and cross-user reads return no content.

- [ ] **Step 5: Commit planning persistence**

```bash
git add supabase/migrations/202608050003_planning.sql agent-service/app/models/planning.py agent-service/app/repositories/planning.py agent-service/tests/planning
git commit -m "feat(planning): add encrypted versioned documents"
```

## Task 2: Immutable Document Proposal Approvals

**Files:**
- Create: `agent-service/app/planning/{schemas,service,router}.py`
- Modify: `agent-service/app/chat/service.py`
- Test: `agent-service/tests/planning/test_approvals.py`, `agent-service/tests/planning/test_api.py`

**Interfaces:**
- Consumes: `DocumentRepository` from Task 1 and `RequestContext` from foundation.
- Produces: `create_document_draft(...) -> DraftApproval`, `decide_document_approval(context, approval_id, decision, edited_payload, idempotency_key) -> ApprovalResult`, `POST /internal/v1/approvals/{id}/decisions`.

- [ ] **Step 1: Write failing Approval tests**

```python
async def test_edit_confirm_preserves_original_and_writes_edited_version(service, user_context):
    draft = await service.create_document_draft(user_context, type="interest", title="original", body="model")
    result = await service.decide_document_approval(user_context, draft.approval_id, "approve", {"title": "edited", "body": "user"}, "decision-1")
    assert result.document.title == "edited"
    assert result.original_payload["title"] == "original"

async def test_second_decision_returns_conflict(service, user_context, pending_approval):
    await service.decide_document_approval(user_context, pending_approval, "reject", None, "decision-1")
    with pytest.raises(ApiError, match="APPROVAL_CONFLICT"):
        await service.decide_document_approval(user_context, pending_approval, "approve", None, "decision-2")
```

- [ ] **Step 2: Run Approval tests to verify they fail**

Run: `cd agent-service && uv run pytest tests/planning/test_approvals.py tests/planning/test_api.py -v`

Expected: FAIL because decisions and API routes are absent.

- [ ] **Step 3: Implement draft creation and decision transactions**

Store a canonical encrypted payload and SHA-256 hash when creating an Approval. For `approve`, write the final document/version, resolve the Approval, append audit data, and emit `run.completed` in one transaction. For `reject`, preserve the original draft and resolve only the Approval. For `regenerate`, mark the draft superseded and create a child run that is linked to the original run.

```python
async with repository.transaction(context) as tx:
    approval = await tx.lock_pending_approval(approval_id)
    final_payload = edited_payload or cipher.decrypt_json(approval.payload_ciphertext)
    document = await tx.activate_document(final_payload)
    await tx.append_version(document, final_payload, context.user_id)
    approval.resolve("approved", context.user_id)
    await tx.audit("document.approved", document.id)
```

- [ ] **Step 4: Run Approval and API tests**

Run: `cd agent-service && uv run pytest tests/planning/test_approvals.py tests/planning/test_api.py -v`

Expected: PASS; repeated `Idempotency-Key` values return the original result and no duplicate version is created.

- [ ] **Step 5: Commit document Approval behavior**

```bash
git add agent-service/app/planning agent-service/app/chat/service.py agent-service/tests/planning
git commit -m "feat(approval): add immutable planning decisions"
```

## Task 3: Real Planning Repository and Approval Cards

**Files:**
- Modify: `web/src/lib/domain/types.ts`, `web/src/lib/domain/repositories.ts`, `web/src/lib/api/real-planning-repository.ts`
- Modify: `web/src/components/approval/PlanDraftCard.tsx`, `web/src/components/approval/ApprovalActions.tsx`, `web/src/components/approval/InlinePlanEditor.tsx`
- Test: `web/src/lib/api/__tests__/real-planning-repository.test.ts`, `web/src/components/approval/__tests__/PlanDraftCard.test.tsx`

**Interfaces:**
- Consumes: `/api/v1/documents`, `/api/v1/approvals/{id}/decisions`.
- Produces: `PlanningRepository.decideApproval(input: ApprovalDecisionInput): Promise<ApprovalResult>` where `ApprovalDecisionInput` includes `approvalId`, `decision`, `editedPayload`, and `idempotencyKey`.

- [ ] **Step 1: Write failing repository and card tests**

```ts
it("sends edited draft payload once when confirming", async () => {
  await repository.decideApproval({ approvalId: "app-1", decision: "approve", editedPayload: { title: "edited", content: "body" }, idempotencyKey: "req-1" });
  expect(fetch).toHaveBeenCalledWith("/api/v1/approvals/app-1/decisions", expect.objectContaining({ method: "POST" }));
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && pnpm test -- real-planning-repository.test.ts PlanDraftCard.test.tsx`

Expected: FAIL because the existing repository only posts a string decision and card edits are not transmitted.

- [ ] **Step 3: Implement server-result-driven UI states**

Use the server response to update title/content/version. Map `APPROVAL_EXPIRED` to a non-retriable expired card, `APPROVAL_CONFLICT` to a refresh action, and `VALIDATION_FAILED` to inline field errors. Preserve edited form values after a transport failure; do not add a local document before the API confirms it.

```ts
const result = await planningRepo.decideApproval(input);
updateDraft(draft.id, { status: "confirmed", title: result.document.title, content: result.document.content });
```

- [ ] **Step 4: Run component and repository tests**

Run: `cd web && pnpm test -- real-planning-repository.test.ts PlanDraftCard.test.tsx`

Expected: PASS; rejected drafts leave the formal planning list unchanged.

- [ ] **Step 5: Commit the real Approval UI**

```bash
git add web/src/lib/domain web/src/lib/api/real-planning-repository.ts web/src/components/approval web/src/lib/api/__tests__
git commit -m "feat(web): connect planning approvals to real API"
```

## Task 4: Document Version UI and End-to-End Verification

**Files:**
- Modify: `web/src/components/planning/PlanningList.tsx`, `web/src/components/planning/PlanningDocument.tsx`, `web/src/components/planning/VersionHistory.tsx`
- Create: `web/e2e/approval-and-versions.spec.ts`
- Create: `web/e2e/support/planning.ts`
- Modify: `web/playwright.config.ts`

**Interfaces:**
- Consumes: `PlanningRepository.listDocuments`, `listVersions`, `restoreVersion`.
- Produces: E2E coverage for draft confirmation and immutable version restoration against a real BFF/API stack.

- [ ] **Step 1: Write a failing real-stack Playwright workflow**

```ts
test("editing a draft creates a version that can be restored as a new version", async ({ page }) => {
  await loginAsSeededUser(page);
  await createDraftAndConfirmEditedContent(page, "edited goal");
  await page.goto("/planning");
  await restoreFirstVersion(page);
  await expect(page.getByText("版本 2")).toBeVisible();
});
```

```ts
export async function loginAsSeededUser(page: Page) { await page.goto("/login"); await page.getByRole("button", { name: "登录" }).click(); }
export async function createDraftAndConfirmEditedContent(page: Page, title: string) { await page.getByRole("button", { name: "编辑后再确认" }).click(); await page.getByLabel("标题").fill(title); await page.getByRole("button", { name: "保存并确认" }).click(); }
export async function restoreFirstVersion(page: Page) { await page.getByRole("button", { name: "查看历史版本" }).click(); await page.getByRole("button", { name: "恢复此版本" }).first().click(); }
```

- [ ] **Step 2: Run the E2E test to verify it fails**

Run: `cd web && pnpm test:e2e -- approval-and-versions.spec.ts`

Expected: FAIL until the BFF/API test stack is started and the version UI consumes real data.

- [ ] **Step 3: Implement real-data list, history, and restore behavior**

Display formal documents only. Require a restore action to call the API, refresh the current document and history, and announce the created version. Do not render raw error payloads or decrypted ciphertext in an error state.

```ts
await planningRepo.restoreVersion(documentId, versionId);
await queryClient.invalidateQueries({ queryKey: ["document", documentId] });
await queryClient.invalidateQueries({ queryKey: ["versions", documentId] });
```

- [ ] **Step 4: Run frontend checks and the real-stack E2E suite**

Run: `cd web && pnpm lint && pnpm test && pnpm test:e2e -- approval-and-versions.spec.ts`

Expected: PASS; no pending draft appears as an active planning document.

- [ ] **Step 5: Commit planning verification**

```bash
git add web/src/components/planning web/e2e/approval-and-versions.spec.ts web/playwright.config.ts
git commit -m "test(web): verify real planning versions and approvals"
```
