// ============================================================
// Auth store — manages login state with Zustand
// ============================================================

import { create } from "zustand";
import type { User } from "@/lib/domain/types";
import type { AuthRepository } from "@/lib/domain/repositories";

interface AuthStore {
  user: User | null;
  loading: boolean;
  error: string | null;
  login: (repo: AuthRepository, email: string, password: string) => Promise<void>;
  logout: (repo: AuthRepository) => Promise<void>;
  checkSession: (repo: AuthRepository) => Promise<void>;
  clearError: () => void;
}

export const useAuthStore = create<AuthStore>((set) => ({
  user: null,
  loading: true,
  error: null,

  async login(repo: AuthRepository, email: string, password: string) {
    set({ loading: true, error: null });
    try {
      const user = await repo.login(email, password);
      set({ user, loading: false });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : "登录失败", loading: false });
      throw e;
    }
  },

  async logout(repo: AuthRepository) {
    await repo.logout();
    set({ user: null });
  },

  async checkSession(repo: AuthRepository) {
    try {
      const user = await repo.getCurrentUser();
      set({ user, loading: false });
    } catch {
      set({ user: null, loading: false });
    }
  },

  clearError() {
    set({ error: null });
  },
}));
