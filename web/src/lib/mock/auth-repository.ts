// ============================================================
// Mock Auth Repository
// ============================================================

import type { AuthRepository } from "@/lib/domain/repositories";
import type { User } from "@/lib/domain/types";

const MOCK_USER: User = {
  id: "user_1",
  name: "苟蛋用户",
  email: "user@goudan.app",
  avatarUrl: "",
};

const MOCK_CREDENTIALS = {
  email: "user@goudan.app",
  password: "123456",
};

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export function createMockAuthRepository(): AuthRepository {
  let currentUser: User | null = null;

  return {
    async login(email: string, password: string): Promise<User> {
      await delay(500);

      if (email === MOCK_CREDENTIALS.email && password === MOCK_CREDENTIALS.password) {
        currentUser = { ...MOCK_USER };
        return currentUser;
      }

      throw new Error("邮箱或密码错误");
    },

    async logout(): Promise<void> {
      await delay(100);
      currentUser = null;
    },

    async getCurrentUser(): Promise<User | null> {
      await delay(50);
      return currentUser ? { ...currentUser } : null;
    },
  };
}
