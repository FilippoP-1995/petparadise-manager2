import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";

/** Lookup di sola lettura condivisi tra domini (Pratiche, Ritiri,
 * Riconsegne) - un solo posto, non una copia per ciascun form. */

export function useCollaborators() {
  return useQuery({
    queryKey: ["references", "collaborators"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/references/collaborators");
      if (error) throw new Error("Impossibile caricare i collaboratori");
      return data;
    },
    staleTime: 5 * 60_000,
  });
}

export function useCalendarZones() {
  return useQuery({
    queryKey: ["references", "calendar-zones"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/references/calendar-zones");
      if (error) throw new Error("Impossibile caricare le zone");
      return data;
    },
    staleTime: 5 * 60_000,
  });
}
