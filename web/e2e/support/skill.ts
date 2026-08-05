// ============================================================
// Real-stack skill-execution E2E helpers.
//
// `createWriteSkillDocument` creates a REAL write-skill document through the BFF
// (draft-create with type='skill' + a write-step manifest, then approve) — the
// same approach the planning E2E uses, no ciphertext seeding. `startSeededWriteSkill`
// drives the UI's "执行 Skill" button, and `expireExecutionApproval` hits the
// TEST-ONLY backend endpoint (gated by AGENT_TEST_MODE=true) that backdates the
// caller's newest pending execution approval so the UI can prove an expired
// decision never queues a run.
// ============================================================

import { type APIRequestContext, type Page, expect } from "@playwright/test";
import { cleanPlanningDocuments } from "./planning";
import { serviceRoleEnv } from "./setup";

const SKILL_TITLE = "e2e write skill";

// A write-step manifest with LITERAL input values (no {{placeholders}}), so the UI
// can request an execution with empty inputs. A document.create step routes to a
// 15-minute execution approval.
const WRITE_MANIFEST = {
  schema_version: 1,
  steps: [
    {
      id: "s1",
      tool: "document.create",
      input: { type: "task", title: "created by skill e2e", body: "written by the e2e skill" },
    },
  ],
};

/** The draft-create + decision envelope shapes (see planning router `_draft_to_dict`). */
interface CreatedDraft {
  approvalId: string;
  documentId: string | null;
  type: string;
  title: string;
}

interface DecisionEnvelope {
  document?: { id: string } | null;
}

/**
 * Create a real write-skill document via the BFF. `request` must carry the
 * logged-in session cookies (pass `page.request`).
 */
export async function createWriteSkillDocument(
  request: APIRequestContext,
): Promise<{ documentId: string; title: string }> {
  const draftRes = await request.post("/api/v1/internal/v1/approvals", {
    data: { type: "skill", title: SKILL_TITLE, body: JSON.stringify(WRITE_MANIFEST) },
  });
  expect(draftRes.ok(), `create skill draft failed: ${draftRes.status()} ${await draftRes.text()}`).toBeTruthy();
  const draft = (await draftRes.json()) as CreatedDraft;

  const decisionRes = await request.post(
    `/api/v1/internal/v1/approvals/${draft.approvalId}/decisions`,
    {
      data: {
        decision: "approve",
        edited_payload: null,
        idempotency_key: `skill-approve-${draft.approvalId}`,
      },
    },
  );
  expect(decisionRes.ok(), `approve skill draft failed: ${decisionRes.status()} ${await decisionRes.text()}`).toBeTruthy();
  const decision = (await decisionRes.json()) as DecisionEnvelope;
  const documentId = decision.document?.id ?? "";
  expect(documentId, "approve must activate the skill document").toBeTruthy();

  return { documentId, title: SKILL_TITLE };
}

/** The brief's Step-1 helper: start the seeded write skill from the /planning page. */
export async function startSeededWriteSkill(page: Page): Promise<void> {
  await page.getByRole("button", { name: "执行 Skill" }).click();
}

/** Expire the caller's newest pending execution approval via the test-mode endpoint. */
export async function expireExecutionApproval(page: Page): Promise<void> {
  const res = await page.request.post("/api/v1/internal/v1/test/expire-latest-approval");
  expect(res.ok(), `expire failed: ${res.status()} ${await res.text()}`).toBeTruthy();
}

/**
 * Remove the seeded user's skill-execution rows for a clean per-test slate.
 * `documents` cascade their execution approvals + sandbox runs (FK on delete
 * cascade), but outbox events and runs are also deleted explicitly so a prior
 * run can never leak into the next test.
 */
export async function cleanSkillExecution(
  api: APIRequestContext,
  userId: string,
): Promise<void> {
  const { url, key } = serviceRoleEnv();
  const base = `${url}/rest/v1`;
  const headers = { apikey: key, Authorization: `Bearer ${key}` };

  await api.delete(`${base}/outbox_events?user_id=eq.${userId}`, { headers });
  await api.delete(`${base}/sandbox_runs?user_id=eq.${userId}`, { headers });
  await api.delete(`${base}/execution_approvals?user_id=eq.${userId}`, { headers });
  await cleanPlanningDocuments(api, userId);
}
