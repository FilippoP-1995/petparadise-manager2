import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type Invoice = components["schemas"]["InvoiceRead"];
export type InvoiceCreateInput = components["schemas"]["InvoiceCreate"];
export type InvoiceReconciliation = components["schemas"]["InvoiceReconciliationRead"];

export function useInvoices(params: { q?: string; practiceId?: number; offset?: number }) {
  return useQuery({
    queryKey: ["invoices", params],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/invoices", {
        params: { query: { q: params.q || undefined, practice_id: params.practiceId, offset: params.offset, limit: 50 } },
      });
      if (error) throw new Error("Impossibile caricare le fatture");
      return data;
    },
  });
}

export function useInvoicesForPractice(practiceId: number) {
  return useInvoices({ practiceId, offset: 0 });
}

export function useInvoice(invoiceId: number) {
  return useQuery({
    queryKey: ["invoices", invoiceId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/invoices/{invoice_id}", { params: { path: { invoice_id: invoiceId } } });
      if (error) throw new Error("Fattura non trovata");
      return data;
    },
    enabled: Number.isFinite(invoiceId),
  });
}

export function useInvoiceReconciliation(invoiceId: number) {
  return useQuery({
    queryKey: ["invoices", invoiceId, "riconciliazione"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/invoices/{invoice_id}/riconciliazione", {
        params: { path: { invoice_id: invoiceId } },
      });
      if (error) throw new Error("Impossibile calcolare la riconciliazione");
      return data;
    },
    enabled: Number.isFinite(invoiceId),
  });
}

export function useCreateInvoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: InvoiceCreateInput) => {
      const { data, error } = await apiClient.POST("/api/invoices", { body: input });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Dati non validi");
      return data;
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
      if (data?.practice_id != null) queryClient.invalidateQueries({ queryKey: ["practices", data.practice_id] });
    },
  });
}

export function useLinkPaymentToInvoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ invoiceId, paymentId }: { invoiceId: number; paymentId: number }) => {
      const { data, error } = await apiClient.POST("/api/invoices/{invoice_id}/collega-pagamento", {
        params: { path: { invoice_id: invoiceId } },
        body: { payment_id: paymentId },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Operazione non consentita");
      return data;
    },
    onSuccess: (_data, vars) => {
      queryClient.invalidateQueries({ queryKey: ["invoices", vars.invoiceId] });
      queryClient.invalidateQueries({ queryKey: ["invoices", vars.invoiceId, "riconciliazione"] });
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
  });
}
