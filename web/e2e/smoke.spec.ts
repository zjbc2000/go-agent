import { test, expect } from "@playwright/test";

test.describe("Goudan App Smoke Tests", () => {
  test("redirects / to /chat", async ({ page }) => {
    await page.goto("/");
    await page.waitForURL("**/chat/**");
    expect(page.url()).toContain("/chat");
  });

  test("login page renders and allows login", async ({ page }) => {
    await page.goto("/login");
    await expect(page.locator("text=登录苟蛋")).toBeVisible();

    // Pre-filled mock credentials
    await page.getByRole("button", { name: "登录" }).click();
    await page.waitForURL("**/chat/**");
    expect(page.url()).toContain("/chat");
  });

  test("chat page shows sidebar navigation", async ({ page }) => {
    // Login first
    await page.goto("/login");
    await page.getByRole("button", { name: "登录" }).click();
    await page.waitForURL("**/chat/**");

    // Sidebar should have the new-chat button visible
    await expect(page.getByRole("button", { name: "新建对话" })).toBeVisible();
  });

  test("planning page loads with documents", async ({ page }) => {
    await page.goto("/planning");
    // Use first match for heading to avoid multi-match
    await expect(page.locator("header h1:has-text('规划')")).toBeVisible({ timeout: 5000 });
    // Mock data should render at least one document
    await expect(page.getByText("学习 Rust 编程")).toBeVisible({ timeout: 5000 });
  });

  test("settings page shows theme switcher and logout", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.getByText("主题设置")).toBeVisible();
    await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  });

  test("dark theme is applied by default", async ({ page }) => {
    await page.goto("/settings");
    const html = page.locator("html");
    await expect(html).toHaveClass(/dark/);
  });

  test("desktop layout at 1440px has sidebar nav buttons", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/planning");
    // Sidebar nav buttons (expanded mode at 1440px)
    await expect(page.getByRole("button", { name: "新建对话" })).toBeVisible();
    await expect(page.getByRole("button", { name: "收起侧栏" })).toBeVisible();
  });
});
