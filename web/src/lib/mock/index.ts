// ============================================================
// Mock repository bundle factory
// ============================================================

import type { RepositoryBundle } from "@/lib/providers/repository-context";
import { createMockAuthRepository } from "./auth-repository";
import { createMockChatRepository } from "./chat-repository";
import { createMockCompanyRepository } from "./company-repository";
import { createMockPlanningRepository } from "./planning-repository";

export function createMockRepositories(): RepositoryBundle {
  return {
    auth: createMockAuthRepository(),
    chat: createMockChatRepository(),
    planning: createMockPlanningRepository(),
    company: createMockCompanyRepository(),
  };
}
