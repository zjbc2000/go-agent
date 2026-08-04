// ============================================================
// Real Auth Repository — placeholder, replace when API is ready
// ============================================================

import type { AuthRepository } from "@/lib/domain/repositories";
import type { User } from "@/lib/domain/types";

export function createRealAuthRepository(): AuthRepository {
  const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

  return {
    async login(email: string, password: string): Promise<User> {
      const res = await fetch(`${API_BASE}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
        credentials: "include",
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ message: "登录失败" }));
        throw new Error(err.message ?? "登录失败");
      }
      return res.json();
    },

    async logout(): Promise<void> {
      await fetch(`${API_BASE}/api/auth/logout`, {
        method: "POST",
        credentials: "include",
      });
    },

    async getCurrentUser(): Promise<User | null> {
      const res = await fetch(`${API_BASE}/api/auth/me`, {
        credentials: "include",
      });
      if (!res.ok) return null;
      return res.json();
    },
  };
}
