import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type Practice = components["schemas"]["PracticeRead"];
export type PracticeCreateInput = components["schemas"]["PracticeCreate"];
export type PracticeUpdateInput = components["schemas"]["PracticeUpdate"];
export type PracticeStatusValue = components["schemas"]["PracticeStatus"];

export function usePractices(params: { q?: string; status?: string; offset?: number; clientId?: number }) {
  return useQuery({
    queryKey: ["practices", params],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/practices", {
        params: {
          query: {
            q: params.q || undefined,
            status: params.status || undefined,
            client_id: params.clientId,
            offset: params.offset,
            limit: 50,
          },
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

// V1 (dettaglio pratica: sidebar 'Cronologia modifiche') - stessa
// cronologia, letta dall'audit_log unificato invece che da una tabella
// practice_history dedicata.
export function usePracticeHistory(practiceId: number) {
  return useQuery({
    queryKey: ["practices", practiceId, "cronologia"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/practices/{practice_id}/cronologia", {
        params: { path: { practice_id: practiceId } },
      });
      if (error) throw new Error("Impossibile caricare la cronologia");
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

// V1 (edit_page, app.py:17293): stesso endpoint di modifica gia' reale
// lato backend (PUT /api/practices/{id}) - qui esposto in un hook
// dedicato invece che dentro useCreatePractice, per tenere ben separati i
// due payload (creazione vs modifica) come gia' fatto per gli altri
// domini in questa sessione (Clienti/Veterinari).
export function useUpdatePractice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ practiceId, input }: { practiceId: number; input: PracticeUpdateInput }) => {
      const { data, error } = await apiClient.PUT("/api/practices/{practice_id}", {
        params: { path: { practice_id: practiceId } },
        body: input,
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Dati non validi");
      return data;
    },
    onSuccess: (_data, vars) => {
      queryClient.invalidateQueries({ queryKey: ["practices"] });
      queryClient.invalidateQueries({ queryKey: ["practices", vars.practiceId] });
      queryClient.invalidateQueries({ queryKey: ["practices", vars.practiceId, "cronologia"] });
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

// V1 (elenco Pratiche, colonna "Etichetta"): stesso endpoint di sola
// lettura gia' usato dal form Pratica per popolare la select tag - qui
// riusato per risolvere gli id in practice.tags a un'etichetta leggibile,
// nessun nuovo endpoint.
export function useTags() {
  return useQuery({
    queryKey: ["references", "tags"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/references/tags");
      if (error) throw new Error("Impossibile caricare le etichette");
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
      queryClient.invalidateQueries({ queryKey: ["practices", "cestino"] });
    },
  });
}

// V1 (trash_page, app.py:18307): Cestino - pratiche cestinate ma ancora
// recuperabili. useRestorePractice riusa /restore, gia' reale ed esistente
// (usato finora solo dal backend), qui la prima UI che lo raggiunge.
export function useTrashedPractices() {
  return useQuery({
    queryKey: ["practices", "cestino"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/practices/trashed");
      if (error) throw new Error("Impossibile caricare il Cestino");
      return data;
    },
  });
}

export function useRestorePractice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (practiceId: number) => {
      const { data, error } = await apiClient.POST("/api/practices/{practice_id}/restore", {
        params: { path: { practice_id: practiceId } },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Operazione non consentita");
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["practices"] });
      queryClient.invalidateQueries({ queryKey: ["practices", "cestino"] });
    },
  });
}
