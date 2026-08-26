import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type Pickup = components["schemas"]["PickupRead"];
export type PickupCreateInput = components["schemas"]["PickupCreate"];
export type PickupStatusValue = components["schemas"]["PickupStatus"];

export function usePickups(params: { q?: string; status?: string; offset?: number }) {
  return useQuery({
    queryKey: ["pickups", params],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/pickups", {
        params: {
          query: { q: params.q || undefined, status: params.status || undefined, offset: params.offset, limit: 50 },
        },
      });
      if (error) throw new Error("Impossibile caricare i ritiri");
      return data;
    },
  });
}

export function usePickup(pickupId: number) {
  return useQuery({
    queryKey: ["pickups", pickupId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/pickups/{pickup_id}", {
        params: { path: { pickup_id: pickupId } },
      });
      if (error) throw new Error("Ritiro non trovato");
      return data;
    },
    enabled: Number.isFinite(pickupId),
  });
}

export function useCreatePickup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: PickupCreateInput) => {
      const { data, error } = await apiClient.POST("/api/pickups", { body: input });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Dati non validi");
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pickups"] }),
  });
}

function useInvalidatingMutation<TVars>(mutationFn: (vars: TVars) => Promise<Pickup>, pickupId: (vars: TVars) => number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: (_data, vars) => {
      queryClient.invalidateQueries({ queryKey: ["pickups"] });
      queryClient.invalidateQueries({ queryKey: ["pickups", pickupId(vars)] });
    },
  });
}

export function useTransitionPickup() {
  return useInvalidatingMutation<{ pickupId: number; targetStatus: PickupStatusValue }>(
    async ({ pickupId, targetStatus }) => {
      const { data, error } = await apiClient.POST("/api/pickups/{pickup_id}/transition", {
        params: { path: { pickup_id: pickupId } },
        body: { target_status: targetStatus },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Transizione non valida");
      return data;
    },
    (vars) => vars.pickupId,
  );
}

export function useCancelPickup() {
  return useInvalidatingMutation<{ pickupId: number; reason?: string }>(
    async ({ pickupId, reason }) => {
      const { data, error } = await apiClient.POST("/api/pickups/{pickup_id}/cancel", {
        params: { path: { pickup_id: pickupId } },
        body: { reason: reason ?? null },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Operazione non consentita");
      return data;
    },
    (vars) => vars.pickupId,
  );
}

export function useCancelPickupAndTrashPractice() {
  return useInvalidatingMutation<{ pickupId: number; reason: string }>(
    async ({ pickupId, reason }) => {
      const { data, error } = await apiClient.POST("/api/pickups/{pickup_id}/cancel-and-trash-practice", {
        params: { path: { pickup_id: pickupId } },
        body: { reason },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Operazione non consentita");
      return data;
    },
    (vars) => vars.pickupId,
  );
}

export function useCreatePracticeFromPickup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      pickupId,
      destinationBranchId,
      serviceType,
    }: {
      pickupId: number;
      destinationBranchId: number;
      serviceType: string;
    }) => {
      const { data, error } = await apiClient.POST("/api/pickups/{pickup_id}/create-practice", {
        params: { path: { pickup_id: pickupId } },
        body: { destination_branch_id: destinationBranchId, service_type: serviceType },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Impossibile creare la pratica");
      return data;
    },
    onSuccess: (_data, vars) => {
      queryClient.invalidateQueries({ queryKey: ["pickups"] });
      queryClient.invalidateQueries({ queryKey: ["pickups", vars.pickupId] });
      queryClient.invalidateQueries({ queryKey: ["practices"] });
    },
  });
}
