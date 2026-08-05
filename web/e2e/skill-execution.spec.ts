import { expect, test } from "@playwright/test";
import { E2E_SESSION_ID, E2E_USER, login } from "./support/auth";
import { ensureSession } from "./support/setup";
import {
  cleanSkillExecution,
  createWriteSkillDocument,
  expireExecutionApproval,
  startSeededWriteSkill,
} from "./support/skill";

// The seed user's id (supabase/seed.sql).
const E2E_USER_ID = "11111111-1111-4111-8111-111111111111";

test.describe("real skill execution approvals (BFF -> agent-service)", () => {
  test.beforeEach(async ({ request }) => {
    // agent-service pytest runs truncate the chat tables, which deletes the seeded
    // session; re-ensure it so the chat page renders, then start from a clean slate.
    await ensureSession(request, E2E_SESSION_ID, E2E_USER_ID);
    await cleanSkillExecution(request, E2E_USER_ID);
  });

  test("a document write waits for approval and an expired decision does not run", async ({
    page,
  }) => {
    await login(page, E2E_USER);
    await createWriteSkillDocument(page.request);
    await page.goto("/planning");

    await startSeededWriteSkill(page);
    await expect(page.getByText("等待确认")).toBeVisible({ timeout: 15000 });

    await expireExecutionApproval(page);
    await page.getByRole("button", { name: "确认执行" }).click();
    await expect(page.getByText("该执行确认已过期")).toBeVisible({ timeout: 15000 });
  });
});
