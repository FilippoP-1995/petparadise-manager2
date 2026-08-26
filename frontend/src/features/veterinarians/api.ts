import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type Veterinarian = components["schemas"]["VeterinarianRead"];
export type VeterinarianCreateInput = components["schemas"]["VeterinarianCreate"];

export function useVeterinarians(params: { q?: string; offset?: number }) {
  return useQuery({
    queryKey: ["veterinarians", params],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/veterinarians", {
        params: { query: { q: params.q || undefined, offset: params.offset, limit: 50 } },
      });
      if (error) throw new Error("Impossibile caricare i veterinari");
      return data;
    },
  });
}

export function useCreateVeterinarian() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: VeterinarianCreateInput) => {
      const { data, error } = await apiClient.POST("/api/veterinarians", { body: input });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Dati non validi");
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["veterinarians"] });
    },
  });
}

export function useDeactivateVeterinarian() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (veterinarianId: number) => {
      const { data, error } = await apiClient.POST("/api/veterinarians/{veterinarian_id}/disattiva", {
        params: { path: { veterinarian_id: veterinarianId } },
      });
      if (error) throw new Error("Operazione non consentita");
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["veterinarians"] });
    },
  });
}
