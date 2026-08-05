import { expect, test } from "@playwright/test";
import { E2E_SESSION_ID, E2E_USER, login } from "./support/auth";
import { ensureSession } from "./support/setup";
import {
  cleanPlanningDocuments,
  createDraftAndConfirmEditedContent,
  restoreFirstVersion,
} from "./support/planning";

// The seed user's id (supabase/seed.sql).
const E2E_USER_ID = "11111111-1111-4111-8111-111111111111";
const EDITED_TITLE = "edited goal";

// The BFF proxy path for a version restore (browser calls are same-origin).
const RESTORE_PATH = /\/api\/v1\/internal\/v1\/documents\/[^/]+\/versions\/[^/]+\/restore$/;

test.describe("real planning approvals and immutable versions (BFF -> agent-service)", () => {
  test.beforeEach(async ({ request }) => {
    // agent-service pytest runs truncate the chat tables, which deletes the seeded
    // session; re-ensure it so the chat page renders, then start from a clean slate.
    await ensureSession(request, E2E_SESSION_ID, E2E_USER_ID);
    await cleanPlanningDocuments(request, E2E_USER_ID);
  });

  test("editing a draft creates a version that can be restored as a new version", async ({
    page,
  }) => {
    await login(page, E2E_USER);

    let restorePosts = 0;
    page.on("request", (req) => {
      if (req.method() === "POST" && RESTORE_PATH.test(req.url())) {
        restorePosts += 1;
      }
    });

    // Creates a genuine pending draft, asserts it does NOT appear in /planning,
    // then edit-confirms it with the edited title on the chat page.
    await createDraftAndConfirmEditedContent(page, EDITED_TITLE);

    // The confirmed document now appears in /planning as 版本 1 (status='active').
    await page.goto("/planning");
    await expect(page.getByText(EDITED_TITLE)).toBeVisible();
    await expect(page.getByText("版本 1")).toBeVisible();

    // Restore the first version — the backend appends a NEW current version.
    await restoreFirstVersion(page);

    // Exactly one restore API call, and a new version row was created (版本 2).
    expect(restorePosts).toBe(1);
    await expect(page.getByText("版本 2")).toBeVisible();

    // The version history refreshed after the restore: both versions are listed.
    await expect(page.getByRole("button", { name: "恢复版本 2" })).toBeVisible();
    await expect(page.getByRole("button", { name: "恢复版本 1" })).toBeVisible();
  });
});
