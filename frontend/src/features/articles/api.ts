import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type Article = components["schemas"]["ArticleRead"];
export type ArticleOrder = components["schemas"]["ArticleOrderRead"];

export function useArticles() {
  return useQuery({
    queryKey: ["articles"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/articles");
      if (error) throw new Error("Impossibile caricare i prodotti");
      return data;
    },
  });
}

export function useRecentArticleOrders() {
  return useQuery({
    queryKey: ["articles", "orders", "recent"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/api/articles/orders/recent");
      if (error) throw new Error("Impossibile caricare le richieste recenti");
      return data;
    },
  });
}

export function useOrderArticle() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (articleId: number) => {
      const { data, error } = await apiClient.POST("/api/articles/{article_id}/ordina", {
        params: { path: { article_id: articleId } },
      });
      if (error) throw new Error((error as { detail?: string }).detail ?? "Operazione non consentita");
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["articles", "orders", "recent"] }),
  });
}
