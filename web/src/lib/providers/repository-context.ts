// ============================================================
// Repository context — provides mock or real implementations
// ============================================================

import { createContext, useContext } from "react";
import type {
  AuthRepository,
  ChatRepository,
  CompanyRepository,
  PlanningRepository,
} from "@/lib/domain/repositories";

export interface RepositoryBundle {
  auth: AuthRepository;
  chat: ChatRepository;
  planning: PlanningRepository;
  company: CompanyRepository;
}

export const RepositoryContext = createContext<RepositoryBundle | null>(null);

export function useRepositories(): RepositoryBundle {
  const ctx = useContext(RepositoryContext);
  if (!ctx) throw new Error("useRepositories must be used within RepositoryProvider");
  return ctx;
}
