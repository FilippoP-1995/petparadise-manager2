import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type Practice = components["schemas"]["PracticeRead"];
export type PracticeCreateInput = components["schemas"]["PracticeCreate"];
export type PracticeUpdateInput = components["schemas"]["PracticeUpdate"];
export type PracticeStatusValue = components["schemas"]["PracticeStatus"];

export function usePractices(params: { q?: string; status?: string; offset?: number }) {
  return useQuery({
    queryKey: ["practices", params],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/practices", {
        params: {
          query: { q: params.q || undefined, status: params.status || undefined, offset: params.offset, limit: 50 },
        },
      });
      if (error) throw new Error("Impossibile caricare le pratiche");
      return data;
    },
  });
}

export function usePractice(practiceId: number) {
  return useQuery({
    queryKey: ["practices", practiceId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/practices/{practice_id}", {
        params: { path: { practice_id: practiceId } },
      });
      if (error) throw new Error("Pratica non trovata");
      return data;
    },
    enabled: Number.isFinite(practiceId),
  });
}

export function useCreatePractice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: PracticeCreateInput) => {
      const { data, error } = await apiClient.POST("/api/practices", { body: input });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Dati non validi");
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["practices"] });
    },
  });
}

export function useTransitionPractice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ practiceId, targetStatus }: { practiceId: number; targetStatus: PracticeStatusValue }) => {
      const { data, error } = await apiClient.POST("/api/practices/{practice_id}/transition", {
        params: { path: { practice_id: practiceId } },
        body: { target_status: targetStatus },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Transizione non valida");
      return data;
    },
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["practices"] });
      queryClient.invalidateQueries({ queryKey: ["practices", variables.practiceId] });
    },
  });
}

export function useCompanyLocations() {
  return useQuery({
    queryKey: ["references", "company-locations"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/references/company-locations");
      if (error) throw new Error("Impossibile caricare le sedi");
      return data;
    },
    staleTime: 5 * 60_000,
  });
}

function invalidatePractice(queryClient: ReturnType<typeof useQueryClient>, practiceId: number) {
  queryClient.invalidateQueries({ queryKey: ["practices"] });
  queryClient.invalidateQueries({ queryKey: ["practices", practiceId] });
  queryClient.invalidateQueries({ queryKey: ["payments", "practice", practiceId, "riconciliazione"] });
}

export function useSetTotalOverride() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ practiceId, amountCents, reason }: { practiceId: number; amountCents: number; reason: string }) => {
      const { data, error } = await apiClient.POST("/api/practices/{practice_id}/override-total", {
        params: { path: { practice_id: practiceId } },
        body: { amount_cents: amountCents, reason },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Dati non validi");
      return data;
    },
    onSuccess: (_data, vars) => invalidatePractice(queryClient, vars.practiceId),
  });
}

export function useClearTotalOverride() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (practiceId: number) => {
      const { data, error } = await apiClient.POST("/api/practices/{practice_id}/clear-total-override", {
        params: { path: { practice_id: practiceId } },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Operazione non consentita");
      return data;
    },
    onSuccess: (_data, practiceId) => invalidatePractice(queryClient, practiceId),
  });
}

export function useMarkCollaboratorBilled() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (practiceId: number) => {
      const { data, error } = await apiClient.POST("/api/practices/{practice_id}/mark-collaborator-billed", {
        params: { path: { practice_id: practiceId } },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Operazione non consentita");
      return data;
    },
    onSuccess: (_data, practiceId) => invalidatePractice(queryClient, practiceId),
  });
}

export function useTrashPractice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (practiceId: number) => {
      const { data, error } = await apiClient.POST("/api/practices/{practice_id}/trash", {
        params: { path: { practice_id: practiceId } },
        body: { reason: null },
      });
      if (error) throw new Error("Operazione non consentita");
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["practices"] });
    },
  });
}
