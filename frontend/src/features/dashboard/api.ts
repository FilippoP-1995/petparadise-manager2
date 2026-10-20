import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type DashboardSummary = components["schemas"]["DashboardSummaryRead"];
export type DashboardPeriod = "oggi" | "settimana" | "mese";

export function useDashboardSummary(period: DashboardPeriod) {
  return useQuery({
    queryKey: ["dashboard", "summary", period],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/dashboard/summary", { params: { query: { period } } });
      if (error) throw new Error("Impossibile caricare la dashboard");
      return data;
    },
  });
}
