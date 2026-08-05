// ============================================================
// Real-stack planning E2E helpers.
//
// Creates genuine server-side pending drafts through the BFF (`page.request`
// carries the logged-in session cookies), injects them into the dev-only
// `window.__chatStore` seam (see src/app/chat/[sessionId]/page.tsx) so a draft
// card renders, drives the edit-confirm + version-restore workflow against the
// ACTUAL rendered labels, and cleans the seeded user's planning rows between
// tests via service-role REST (following e2e/support/setup.ts patterns).
// ============================================================

import { type APIRequestContext, type Page, expect } from "@playwright/test";
import { E2E_SESSION_ID } from "./auth";
import { serviceRoleEnv } from "./setup";

/** Shape returned by POST /api/v1/internal/v1/approvals (router `_draft_to_dict`). */
export interface CreatedDraft {
  approvalId: string;
  draftId: string;
  documentId: string | null;
  type: string;
  title: string;
  body: string;
}

/** The PlanDraft fields injected into the dev-only chat-store seam. */
interface DraftToInject {
  id: string;
  sessionId: string;
  messageId: string;
  title: string;
  content: string;
  category: string;
  status: string;
  createdAt: string;
}

const ORIGINAL_TITLE = "original goal";
const ORIGINAL_BODY = "original plan body";
const EDITED_CONTENT = "edited plan content";

/**
 * Create a genuine pending approval draft server-side through the BFF. `request`
 * must carry the logged-in session cookies (pass `page.request`).
 */
export async function createDraft(
  request: APIRequestContext,
  draft: { type: string; title: string; body: string },
): Promise<CreatedDraft> {
  const res = await request.post("/api/v1/internal/v1/approvals", { data: draft });
  expect(res.ok(), `create draft failed: ${res.status()} ${await res.text()}`).toBeTruthy();
  return (await res.json()) as CreatedDraft;
}

/**
 * Inject a pending PlanDraft into the Zustand chat store through the dev-only
 * `window.__chatStore` seam so the draft card renders. The page must already be
 * on `/chat/:sessionId` (the seam is mounted by that page).
 */
export async function injectDraft(page: Page, draft: DraftToInject): Promise<void> {
  await page.waitForFunction(() => Boolean((window as { __chatStore?: unknown }).__chatStore));
  await page.evaluate((d) => {
    const seam = (
      window as unknown as {
        __chatStore: {
          getState: () => {
            addDraft: (draft: DraftToInject) => void;
          };
        };
      }
    ).__chatStore;
    seam.getState().addDraft({
      id: d.id,
      sessionId: d.sessionId,
      messageId: d.messageId,
      title: d.title,
      content: d.content,
      category: d.category,
      status: "pending_confirmation",
      createdAt: d.createdAt,
    });
  }, draft);
}

/**
 * The brief's Step-1 workflow against the real stack: create a genuine draft,
 * assert a created-but-UNconfirmed draft does NOT appear in /planning (the
 * backend only lists `status='active'`), render the draft card via the dev-only
 * seam, edit-confirm it with the edited title, and wait for the decision to
 * commit server-side (the card flips to 已确认).
 */
export async function createDraftAndConfirmEditedContent(
  page: Page,
  editedTitle: string,
): Promise<CreatedDraft> {
  const draft = await createDraft(page.request, {
    type: "task",
    title: ORIGINAL_TITLE,
    body: ORIGINAL_BODY,
  });

  // A pending draft is NOT an active planning document.
  await page.goto("/planning");
  await expect(page.getByText(ORIGINAL_TITLE)).toHaveCount(0);

  // Render the draft card through the dev-only store seam on the chat page.
  const sessionId = E2E_SESSION_ID;
  await page.goto(`/chat/${sessionId}`);
  await injectDraft(page, {
    id: draft.approvalId,
    sessionId,
    messageId: `msg-${draft.approvalId}`,
    title: ORIGINAL_TITLE,
    content: ORIGINAL_BODY,
    category: draft.type,
    status: "pending_confirmation",
    createdAt: new Date().toISOString(),
  });
  await expect(page.getByText(ORIGINAL_TITLE)).toBeVisible();

  // Edit then confirm with the edited title (real labels: placeholder 标题/内容,
  // save button 保存).
  await page.getByRole("button", { name: "编辑后再确认" }).click();
  await page.getByPlaceholder("标题").fill(editedTitle);
  await page.getByPlaceholder("内容").fill(EDITED_CONTENT);
  await page.getByRole("button", { name: "保存", exact: true }).click();

  // The confirmation has committed when the card shows the 已确认 badge.
  await expect(page.getByText("已确认")).toBeVisible({ timeout: 15000 });

  return draft;
}

/** Open the version history on the current /planning document and restore version 1. */
export async function restoreFirstVersion(page: Page): Promise<void> {
  await page.getByRole("button", { name: "查看历史版本" }).click();
  await page.getByRole("button", { name: "恢复版本 1" }).first().click();
}

/**
 * Remove the seeded user's planning rows for a clean per-test slate.
 * `document_drafts` (document_id may be NULL for new proposals) and `audit_logs`
 * (document_id is on delete set null) are not cascaded by a documents delete, so
 * they are deleted explicitly; documents cascade versions and any approvals
 * linked by document_id, and drafts cascade approvals via draft_id.
 */
export async function cleanPlanningDocuments(
  api: APIRequestContext,
  userId: string,
): Promise<void> {
  const { url, key } = serviceRoleEnv();
  const base = `${url}/rest/v1`;
  const headers = { apikey: key, Authorization: `Bearer ${key}` };

  await api.delete(`${base}/audit_logs?user_id=eq.${userId}`, { headers });
  await api.delete(`${base}/document_drafts?user_id=eq.${userId}`, { headers });
  await api.delete(`${base}/documents?user_id=eq.${userId}`, { headers });
}
