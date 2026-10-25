import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          viewer: ["openseadragon", "polygon-clipping"],
          react: ["react", "react-dom", "react-router-dom", "zustand"],
        },
      },
    },
  },
  server: {
    port: 5173,
    // The real API (FastAPI) runs on :8000. The UI uses sample data until it's connected.
    proxy: { "/api": { target: "http://localhost:8000", rewrite: (p) => p.replace(/^\/api/, "") } },
  },
});
