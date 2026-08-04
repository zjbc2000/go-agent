import { describe, it, expect } from "vitest";
import { createMockAuthRepository } from "@/lib/mock/auth-repository";

describe("MockAuthRepository", () => {
  it("logs in with correct credentials", async () => {
    const repo = createMockAuthRepository();
    const user = await repo.login("user@goudan.app", "123456");
    expect(user.name).toBe("苟蛋用户");
    expect(user.email).toBe("user@goudan.app");
  });

  it("rejects wrong password", async () => {
    const repo = createMockAuthRepository();
    await expect(repo.login("user@goudan.app", "wrong")).rejects.toThrow("邮箱或密码错误");
  });

  it("rejects wrong email", async () => {
    const repo = createMockAuthRepository();
    await expect(repo.login("wrong@goudan.app", "123456")).rejects.toThrow("邮箱或密码错误");
  });

  it("getCurrentUser returns null before login", async () => {
    const repo = createMockAuthRepository();
    const user = await repo.getCurrentUser();
    expect(user).toBeNull();
  });

  it("getCurrentUser returns user after login", async () => {
    const repo = createMockAuthRepository();
    await repo.login("user@goudan.app", "123456");
    const user = await repo.getCurrentUser();
    expect(user).not.toBeNull();
    expect(user?.name).toBe("苟蛋用户");
  });

  it("logout clears session", async () => {
    const repo = createMockAuthRepository();
    await repo.login("user@goudan.app", "123456");
    await repo.logout();
    const user = await repo.getCurrentUser();
    expect(user).toBeNull();
  });
});
