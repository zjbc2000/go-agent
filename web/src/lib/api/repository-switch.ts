// ============================================================
// Repository switch — toggle between mock and real implementations
// ============================================================

import type { RepositoryBundle } from "@/lib/providers/repository-context";
import { createMockRepositories } from "@/lib/mock";
import { createRealChatRepository } from "./real-chat-repository";
import { createRealCompanyRepository } from "./real-company-repository";
import { createRealPlanningRepository } from "./real-planning-repository";
import { createRealAuthRepository } from "./real-auth-repository";

/**
 * Set NEXT_PUBLIC_USE_REAL_API=true to switch from mock to real API.
 * Default: mock (safe for development without a running backend).
 */
export function createRepositories(): RepositoryBundle {
  const useReal = process.env.NEXT_PUBLIC_USE_REAL_API === "true";

  if (useReal) {
    return {
      auth: createRealAuthRepository(),
      chat: createRealChatRepository(),
      planning: createRealPlanningRepository(),
      company: createRealCompanyRepository(),
    };
  }

  return createMockRepositories();
}
