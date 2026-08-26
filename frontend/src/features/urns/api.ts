import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type Urn = components["schemas"]["UrnRead"];
export type UrnInput = components["schemas"]["UrnCreate"];
export type UrnCategoryValue = components["schemas"]["UrnCategory"];
export type UrnMovement = components["schemas"]["UrnMovementRead"];

export function useUrns(params: { category?: UrnCategoryValue; activeOnly?: boolean }) {
  return useQuery({
    queryKey: ["urns", params],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/urns", {
        params: { query: { category: params.category, active_only: params.activeOnly } },
      });
      if (error) throw new Error("Impossibile caricare il catalogo urne");
      return data;
    },
  });
}

export function useUrn(urnId: number) {
  return useQuery({
    queryKey: ["urns", urnId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/urns/{urn_id}", { params: { path: { urn_id: urnId } } });
      if (error) throw new Error("Urna non trovata");
      return data;
    },
    enabled: Number.isFinite(urnId),
  });
}

export function useUrnMovements(urnId: number) {
  return useQuery({
    queryKey: ["urns", urnId, "movements"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/urns/{urn_id}/movements", { params: { path: { urn_id: urnId } } });
      if (error) throw new Error("Impossibile caricare lo storico movimenti");
      return data;
    },
    enabled: Number.isFinite(urnId),
  });
}

export function useCreateUrn() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: UrnInput) => {
      const { data, error } = await apiClient.POST("/api/urns", { body: input });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Dati non validi");
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["urns"] }),
  });
}

export function useUpdateUrn() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ urnId, input }: { urnId: number; input: UrnInput }) => {
      const { data, error } = await apiClient.PUT("/api/urns/{urn_id}", { params: { path: { urn_id: urnId } }, body: input });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Dati non validi");
      return data;
    },
    onSuccess: (_data, vars) => {
      queryClient.invalidateQueries({ queryKey: ["urns"] });
      queryClient.invalidateQueries({ queryKey: ["urns", vars.urnId] });
      queryClient.invalidateQueries({ queryKey: ["urns", vars.urnId, "movements"] });
    },
  });
}

export function useDeactivateUrn() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (urnId: number) => {
      const { data, error } = await apiClient.POST("/api/urns/{urn_id}/disattiva", { params: { path: { urn_id: urnId } } });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Operazione non consentita");
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["urns"] }),
  });
}
