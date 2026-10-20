/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": "/src" },
  },
  server: {
    host: true, // ascolta anche sulla rete locale (0.0.0.0), non solo localhost - richiesto per raggiungere il dev server da iPhone in LAN
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8001",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test-setup.ts",
  },
});
