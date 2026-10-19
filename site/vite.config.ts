import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative base: the built site works from any path (GitHub Pages project page, Netlify, Vercel).
export default defineConfig({
  base: "./",
  plugins: [react()],
  worker: { format: "es" },
  build: { target: "es2020", chunkSizeWarningLimit: 1200 },
});
