import { expect, test } from "@playwright/test";
import { E2E_SESSION_ID, E2E_USER, login } from "./support/auth";
import { cleanSession } from "./support/setup";

const FULL_REPLY = "Hello from the Goudan agent!";

test.describe("real chat streaming (BFF -> agent-service)", () => {
  test.beforeEach(async ({ request }) => {
    await cleanSession(request, E2E_SESSION_ID);
  });

  test("streams a reply and reconnects without duplicating the user message", async ({
    page,
  }) => {
    let runPosts = 0;

    // Intercept run-stream POSTs. The first one is forwarded to the real stack so the
    // run is genuinely created and persisted, but only a truncated SSE prefix (no
    // terminal event) is delivered to the browser — simulating a mid-stream network
    // drop. The client must reconnect with the same idempotency key and Last-Event-ID.
    await page.route("**/runs", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      runPosts += 1;
      if (runPosts === 1) {
        const upstream = await route.fetch();
        const fullBody = await upstream.text();
        const prefix = fullBody.split("\n\n").slice(0, 2).join("\n\n") + "\n\n";
        await route.fulfill({
          status: 200,
          headers: { "content-type": "text/event-stream", "cache-control": "no-cache" },
          body: prefix,
        });
        return;
      }
      await route.fulfill({ response: await route.fetch() });
    });

    await login(page, E2E_USER);
    await page.goto(`/chat/${E2E_SESSION_ID}`);

    // Send a message.
    await page.getByPlaceholder("输入你的需求...").fill("你好，苟蛋");
    await page.getByRole("button", { name: "发送消息" }).click();

    // The first token renders from the truncated stream.
    await expect(page.getByText("Hello fr")).toBeVisible({ timeout: 15000 });

    // The client reconnects (same idempotency key) and the assistant reply completes.
    await expect(page.getByText(FULL_REPLY)).toBeVisible({ timeout: 20000 });

    // Exactly one user message is rendered — the reconnect reused the existing run
    // and never created a second user message.
    await expect(page.locator("p.whitespace-pre-wrap")).toHaveCount(1);

    // The client genuinely reconnected rather than a single uninterrupted stream.
    expect(runPosts).toBeGreaterThanOrEqual(2);
  });
});
