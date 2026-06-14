import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig(({ command }) => ({
  plugins: [react()],
  // The built SPA is mounted under /app on the backend, so assets must be
  // referenced as /app/assets/... Dev (and the Playwright spec) stay at root.
  base: command === "build" ? "/app/" : "/",
  server: { host: "127.0.0.1", port: 5173, proxy: { "/api": "http://127.0.0.1:8765" } },
  build: { outDir: "dist", emptyOutDir: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"]
  }
}));
