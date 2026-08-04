"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { RepositoryContext } from "./repository-context";
import { createRepositories } from "@/lib/api/repository-switch";
import { useState } from "react";

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
