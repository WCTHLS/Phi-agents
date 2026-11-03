import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API listens on 8000 everywhere in the docs and scripts. Set VITE_API_URL to point elsewhere.
const backendTarget = process.env.VITE_API_URL || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: backendTarget,
        changeOrigin: true,
      },
      "/health": {
        target: backendTarget,
        changeOrigin: true,
      },
      "/ready": {
        target: backendTarget,
        changeOrigin: true,
      },
    },
  },
});
