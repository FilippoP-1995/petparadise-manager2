import { apiClient } from "@/shared/api/client";

export async function login(username: string, password: string) {
  const { data, error } = await apiClient.POST("/api/auth/login", {
    body: { username, password },
  });
  if (error) throw new Error("Credenziali non valide");
  return data;
}

export async function logout() {
  await apiClient.POST("/api/auth/logout");
}

export async function fetchCurrentUser() {
  const { data, error } = await apiClient.GET("/api/auth/me");
  if (error) return null;
  return data;
}
