import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type CompanyLocation = components["schemas"]["CompanyLocationRead"];
export type CompanyLocationInput = components["schemas"]["CompanyLocationCreate"];

export function useCompanyLocationsAdmin() {
  return useQuery({
    queryKey: ["company-locations", "admin"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/company-locations");
      if (error) throw new Error("Impossibile caricare le sedi");
      return data;
    },
  });
}

export function useCompanyLocation(locationId: number) {
  return useQuery({
    queryKey: ["company-locations", "admin", locationId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/company-locations/{location_id}", {
        params: { path: { location_id: locationId } },
      });
      if (error) throw new Error("Sede non trovata");
      return data;
    },
    enabled: Number.isFinite(locationId),
  });
}

function invalidateLocations(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ["company-locations"] });
  queryClient.invalidateQueries({ queryKey: ["references", "company-locations"] });
}

export function useCreateCompanyLocation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: CompanyLocationInput) => {
      const { data, error } = await apiClient.POST("/api/company-locations", { body: input });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Dati non validi");
      return data;
    },
    onSuccess: () => invalidateLocations(queryClient),
  });
}

export function useUpdateCompanyLocation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ locationId, input }: { locationId: number; input: CompanyLocationInput }) => {
      const { data, error } = await apiClient.PUT("/api/company-locations/{location_id}", {
        params: { path: { location_id: locationId } },
        body: input,
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Dati non validi");
      return data;
    },
    onSuccess: () => invalidateLocations(queryClient),
  });
}

export function useDeactivateCompanyLocation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (locationId: number) => {
      const { data, error } = await apiClient.POST("/api/company-locations/{location_id}/disattiva", {
        params: { path: { location_id: locationId } },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Operazione non consentita");
      return data;
    },
    onSuccess: () => invalidateLocations(queryClient),
  });
}
