// ============================================================
// Real Auth Repository — cookie-backed BFF auth client
// ============================================================

import type { AuthRepository } from "@/lib/domain/repositories";
import type { User } from "@/lib/domain/types";

const AUTH_API = "/api/v1/auth";

/** Typed auth error carrying the BFF error envelope code. */
export class AuthError extends Error {
  readonly code: string;
  readonly retryable: boolean;

  constructor(code: string, message: string, retryable = false) {
    super(message);
    this.name = "AuthError";
    this.code = code;
    this.retryable = retryable;
  }
}

async function toAuthError(res: Response): Promise<AuthError> {
  const body = await res.json().catch(() => null);
  const code = body?.error?.code ?? "INTERNAL_ERROR";
  const message = body?.error?.message ?? "请求失败，请稍后重试。";
  return new AuthError(code, message);
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${AUTH_API}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    throw await toAuthError(res);
  }
  return res.json() as Promise<T>;
}

export function createRealAuthRepository(): AuthRepository {
  return {
    async login(email: string, password: string): Promise<User> {
      return requestJson<User>("/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
    },

    async logout(): Promise<void> {
      await requestJson("/logout", { method: "POST" });
    },

    async getCurrentUser(): Promise<User | null> {
      const res = await fetch(`${AUTH_API}/me`, { credentials: "include" });
      if (res.status === 401) return null;
      if (!res.ok) throw await toAuthError(res);
      return res.json() as Promise<User>;
    },
  };
}
