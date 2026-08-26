import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type CremationCycle = components["schemas"]["CremationCycleRead"];
export type CremationCycleCreateInput = components["schemas"]["CremationCycleCreate"];
export type CycleAnimal = components["schemas"]["CycleAnimalRead"];

export function useCremationCycles(params: { status?: string; cycleDate?: string; offset?: number }) {
  return useQuery({
    queryKey: ["cremation-cycles", params],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/cremation-cycles", {
        params: {
          query: { status: params.status || undefined, cycle_date: params.cycleDate || undefined, offset: params.offset, limit: 50 },
        },
      });
      if (error) throw new Error("Impossibile caricare i cicli di cremazione");
      return data;
    },
  });
}

export function useCremationCycle(cycleId: number) {
  return useQuery({
    queryKey: ["cremation-cycles", cycleId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/cremation-cycles/{cycle_id}", {
        params: { path: { cycle_id: cycleId } },
      });
      if (error) throw new Error("Ciclo non trovato");
      return data;
    },
    enabled: Number.isFinite(cycleId),
  });
}

export function useEligibleAnimals() {
  return useQuery({
    queryKey: ["cremation-cycles", "eligible-animals"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/cremation-cycles/eligible-animals");
      if (error) throw new Error("Impossibile caricare gli animali in attesa di ciclo");
      return data;
    },
  });
}

export function useCreateCremationCycle() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: CremationCycleCreateInput) => {
      const { data, error } = await apiClient.POST("/api/cremation-cycles", { body: input });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Dati non validi");
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cremation-cycles"] }),
  });
}

function useInvalidatingMutation<TVars>(
  mutationFn: (vars: TVars) => Promise<CremationCycle>,
  cycleId: (vars: TVars) => number,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: (_data, vars) => {
      queryClient.invalidateQueries({ queryKey: ["cremation-cycles"] });
      queryClient.invalidateQueries({ queryKey: ["cremation-cycles", cycleId(vars)] });
      // il completamento/ripristino di un ciclo puo' cambiare lo stato di una
      // o piu' pratiche (side-effect automatico) - la lista/dettaglio Pratica
      // deve rispecchiarlo senza un refresh manuale.
      queryClient.invalidateQueries({ queryKey: ["practices"] });
    },
  });
}

export function useDeleteCremationCycle() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (cycleId: number) => {
      const { error } = await apiClient.DELETE("/api/cremation-cycles/{cycle_id}", {
        params: { path: { cycle_id: cycleId } },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Impossibile eliminare il ciclo");
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cremation-cycles"] }),
  });
}

export function useAssignAnimal() {
  return useInvalidatingMutation<{ cycleId: number; animalId: number }>(
    async ({ cycleId, animalId }) => {
      const { data, error } = await apiClient.POST("/api/cremation-cycles/{cycle_id}/assign-animal", {
        params: { path: { cycle_id: cycleId } },
        body: { animal_id: animalId },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Assegnazione non riuscita");
      return data;
    },
    (vars) => vars.cycleId,
  );
}

export function useRemoveAnimal() {
  return useInvalidatingMutation<{ cycleId: number; animalId: number }>(
    async ({ cycleId, animalId }) => {
      const { data, error } = await apiClient.POST("/api/cremation-cycles/{cycle_id}/remove-animal", {
        params: { path: { cycle_id: cycleId } },
        body: { animal_id: animalId },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Rimozione non riuscita");
      return data;
    },
    (vars) => vars.cycleId,
  );
}

export function useCompleteCycle() {
  return useInvalidatingMutation<{ cycleId: number }>(async ({ cycleId }) => {
    const { data, error } = await apiClient.POST("/api/cremation-cycles/{cycle_id}/complete", {
      params: { path: { cycle_id: cycleId } },
    });
    if (error) throw new Error((error as { detail?: string }).detail ?? "Completamento non riuscito");
    return data;
  }, (vars) => vars.cycleId);
}

export function useRevertCycle() {
  return useInvalidatingMutation<{ cycleId: number; reason: string }>(
    async ({ cycleId, reason }) => {
      const { data, error } = await apiClient.POST("/api/cremation-cycles/{cycle_id}/revert", {
        params: { path: { cycle_id: cycleId } },
        body: { reason },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Ripristino non riuscito");
      return data;
    },
    (vars) => vars.cycleId,
  );
}
