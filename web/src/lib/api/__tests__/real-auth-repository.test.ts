import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthError, createRealAuthRepository } from "@/lib/api/real-auth-repository";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const MOCK_USER = {
  id: "user_1",
  name: "苟蛋用户",
  email: "user@goudan.app",
  role: "user",
} as const;

describe("RealAuthRepository", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("posts login to the relative BFF route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(MOCK_USER));
    vi.stubGlobal("fetch", fetchMock);

    const user = await createRealAuthRepository().login("user@goudan.app", "secret");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/login",
      expect.objectContaining({ method: "POST" }),
    );
    expect(user).toEqual(MOCK_USER);
  });

  it("posts logout to the relative BFF route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await createRealAuthRepository().logout();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/logout",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("fetches the current user from the relative BFF route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(MOCK_USER));
    vi.stubGlobal("fetch", fetchMock);

    const user = await createRealAuthRepository().getCurrentUser();

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/auth/me", expect.anything());
    expect(user).toEqual(MOCK_USER);
  });

  it("returns null from getCurrentUser when the BFF responds 401", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ error: { code: "AUTH_REQUIRED", message: "Authentication is required." } }, 401),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(createRealAuthRepository().getCurrentUser()).resolves.toBeNull();
  });

  it("maps the AUTH_REQUIRED envelope to a typed AuthError on login", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ error: { code: "AUTH_REQUIRED", message: "Invalid email or password." } }, 401),
      );
    vi.stubGlobal("fetch", fetchMock);

    const promise = createRealAuthRepository().login("user@goudan.app", "wrong");
    await expect(promise).rejects.toBeInstanceOf(AuthError);
    await expect(promise).rejects.toMatchObject({ code: "AUTH_REQUIRED" });
  });
});
