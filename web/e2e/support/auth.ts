import { type Page } from "@playwright/test";

export interface TestUser {
  email: string;
  password: string;
}

/** Seeded by supabase/seed.sql (password: password123). */
export const E2E_USER: TestUser = {
  email: "e2e@goudan.app",
  password: "password123",
};

/** Session row seeded by supabase/seed.sql, owned by E2E_USER. */
export const E2E_SESSION_ID = "22222222-2222-4222-8222-222222222222";

/** Log in through the BFF login UI and wait for the redirect into /chat. */
export async function login(page: Page, user: TestUser = E2E_USER): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("邮箱", { exact: true }).fill(user.email);
  await page.getByLabel("密码", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  // The post-login redirect is a Next App Router client-side navigation, which never
  // fires a document "load" event; wait for the committed URL instead. Accept either
  // /chat (no sessions) or /chat/<id>. The generous timeout absorbs the cold
  // `pnpm dev` first compile.
  await page.waitForURL(/\/chat(\/.*)?$/, { waitUntil: "commit", timeout: 30000 });
}
