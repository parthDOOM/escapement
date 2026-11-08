import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative base: the built site works from any path (GitHub Pages project page, Netlify, Vercel).
export default defineConfig({
  base: "./",
  plugins: [react()],
  worker: { format: "es" },
  build: {
    target: "es2020",
    chunkSizeWarningLimit: 1200,
    // two pages: the chip (/) and the hardware bench (/fpga/)
    rollupOptions: { input: { main: resolve(__dirname, "index.html"), fpga: resolve(__dirname, "fpga/index.html") } },
  },
});
