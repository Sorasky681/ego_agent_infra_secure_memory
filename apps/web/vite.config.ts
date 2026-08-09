import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const requestedBase = env.VITE_BASE_PATH || "/";
  const base = requestedBase.endsWith("/") ? requestedBase : `${requestedBase}/`;

  return {
    base,
    plugins: [react()],
    server: {
      port: 4173,
      proxy: {
        "/api": "http://localhost:8000",
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: "./src/test/setup.ts",
    },
  };
});
