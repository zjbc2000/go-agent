"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { RepositoryContext } from "./repository-context";
import { createRepositories } from "@/lib/api/repository-switch";
import { useAuthStore } from "@/lib/stores/auth-store";
import { useEffect, useState } from "react";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 30_000, retry: 1 },
    },
  });
}

export function AppProviders({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(makeQueryClient);
  const [repos] = useState(createRepositories);

  // Restore the authenticated user on every full page load (Zustand is not
  // persisted, so without this the auth store is null after a refresh and pages
  // fall back to placeholder accounts).
  useEffect(() => {
    const { auth } = repos;
    useAuthStore.getState().checkSession(auth).catch(() => {});
  }, [repos]);

  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="dark"
      enableSystem={false}
      disableTransitionOnChange
    >
      <QueryClientProvider client={queryClient}>
        <RepositoryContext.Provider value={repos}>
          <TooltipProvider delay={300}>
            {children}
            <Toaster richColors closeButton />
          </TooltipProvider>
        </RepositoryContext.Provider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
