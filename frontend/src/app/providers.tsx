import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { RouterProvider } from "react-router-dom";

import { AuthProvider } from "@/features/auth/AuthContext";

import { router } from "./router";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 10_000 } },
});

export function AppProviders({ children }: { children?: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>{children ?? <RouterProvider router={router} />}</AuthProvider>
    </QueryClientProvider>
  );
}
