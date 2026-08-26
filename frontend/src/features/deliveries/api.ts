import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type Delivery = components["schemas"]["DeliveryRead"];
export type DeliveryCreateInput = components["schemas"]["DeliveryCreate"];

export function useDeliveries(params: { q?: string; offset?: number }) {
  return useQuery({
    queryKey: ["deliveries", params],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/deliveries", {
        params: { query: { q: params.q || undefined, offset: params.offset, limit: 50 } },
      });
      if (error) throw new Error("Impossibile caricare le riconsegne");
      return data;
    },
  });
}

export function useDelivery(deliveryId: number) {
  return useQuery({
    queryKey: ["deliveries", deliveryId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/deliveries/{delivery_id}", {
        params: { path: { delivery_id: deliveryId } },
      });
      if (error) throw new Error("Riconsegna non trovata");
      return data;
    },
    enabled: Number.isFinite(deliveryId),
  });
}

export function useCreateDelivery() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: DeliveryCreateInput) => {
      const { data, error } = await apiClient.POST("/api/deliveries", { body: input });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Dati non validi");
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["deliveries"] }),
  });
}

export function useLinkDeliveryToPractice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      deliveryId,
      practiceId,
      confirmDespiteMismatch,
    }: {
      deliveryId: number;
      practiceId: number;
      confirmDespiteMismatch?: boolean;
    }) => {
      const { data, error } = await apiClient.POST("/api/deliveries/{delivery_id}/link-practice", {
        params: { path: { delivery_id: deliveryId } },
        body: { practice_id: practiceId, confirm_despite_mismatch: confirmDespiteMismatch ?? false },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Impossibile collegare la pratica");
      return data;
    },
    onSuccess: (_data, vars) => {
      queryClient.invalidateQueries({ queryKey: ["deliveries"] });
      queryClient.invalidateQueries({ queryKey: ["deliveries", vars.deliveryId] });
    },
  });
}
