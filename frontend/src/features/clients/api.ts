import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type Client = components["schemas"]["ClientRead"];
export type ClientCreateInput = components["schemas"]["ClientCreate"];

export function useClients(params: { q?: string; offset?: number }) {
  return useQuery({
    queryKey: ["clients", params],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/clients", {
        params: { query: { q: params.q || undefined, offset: params.offset, limit: 50 } },
      });
      if (error) throw new Error("Impossibile caricare i clienti");
      return data;
    },
  });
}

export function useClient(clientId: number) {
  return useQuery({
    queryKey: ["clients", clientId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/clients/{client_id}", {
        params: { path: { client_id: clientId } },
      });
      if (error) throw new Error("Cliente non trovato");
      return data;
    },
  });
}

export function useCreateClient() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: ClientCreateInput) => {
      const { data, error } = await apiClient.POST("/api/clients", { body: input });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Dati non validi");
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["clients"] });
    },
  });
}

export function useDeactivateClient() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (clientId: number) => {
      const { data, error } = await apiClient.POST("/api/clients/{client_id}/disattiva", {
        params: { path: { client_id: clientId } },
      });
      if (error) throw new Error("Operazione non consentita");
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["clients"] });
    },
  });
}
