import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type Payment = components["schemas"]["PaymentRead"];
export type PaymentCreateInput = components["schemas"]["PaymentCreate"];
export type PaymentDeletion = components["schemas"]["PaymentDeletionRead"];
export type PracticeReconciliation = components["schemas"]["PracticeReconciliationRead"];

export function usePaymentsForPractice(practiceId: number) {
  return useQuery({
    queryKey: ["payments", "practice", practiceId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/payments", { params: { query: { practice_id: practiceId } } });
      if (error) throw new Error("Impossibile caricare i pagamenti");
      return data;
    },
    enabled: Number.isFinite(practiceId),
  });
}

export function usePracticeReconciliation(practiceId: number) {
  return useQuery({
    queryKey: ["payments", "practice", practiceId, "riconciliazione"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/payments/practice/{practice_id}/riconciliazione", {
        params: { path: { practice_id: practiceId } },
      });
      if (error) throw new Error("Impossibile calcolare la riconciliazione");
      return data;
    },
    enabled: Number.isFinite(practiceId),
  });
}

function invalidatePracticePayments(queryClient: ReturnType<typeof useQueryClient>, practiceId: number | null | undefined) {
  if (practiceId == null) return;
  queryClient.invalidateQueries({ queryKey: ["payments", "practice", practiceId] });
  queryClient.invalidateQueries({ queryKey: ["practices", practiceId] });
  queryClient.invalidateQueries({ queryKey: ["invoices"] });
}

export function useRegisterPayment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: PaymentCreateInput) => {
      const { data, error } = await apiClient.POST("/api/payments", { body: input });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Dati non validi");
      return data;
    },
    onSuccess: (data) => invalidatePracticePayments(queryClient, data?.practice_id),
  });
}

export function useReversePayment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ paymentId, reason }: { paymentId: number; reason: string }) => {
      const { data, error } = await apiClient.POST("/api/payments/{payment_id}/storna", {
        params: { path: { payment_id: paymentId } },
        body: { reason },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Operazione non consentita");
      return data;
    },
    onSuccess: (data) => invalidatePracticePayments(queryClient, data?.practice_id),
  });
}

export function useDeletePayment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      paymentId,
      deletionKind,
      reason,
      practiceId,
    }: {
      paymentId: number;
      deletionKind: string;
      reason: string;
      practiceId: number | null;
    }) => {
      const { data, error } = await apiClient.POST("/api/payments/{payment_id}/elimina", {
        params: { path: { payment_id: paymentId } },
        body: { deletion_kind: deletionKind, reason },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Operazione non consentita");
      return { ...data, practiceId };
    },
    onSuccess: (data) => invalidatePracticePayments(queryClient, data.practiceId),
  });
}

export function useRestorePaymentDeletion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (deletionId: number) => {
      const { data, error } = await apiClient.POST("/api/payments/deletions/{deletion_id}/ripristina", {
        params: { path: { deletion_id: deletionId } },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Operazione non consentita");
      return data;
    },
    onSuccess: (data) => invalidatePracticePayments(queryClient, data?.practice_id),
  });
}
