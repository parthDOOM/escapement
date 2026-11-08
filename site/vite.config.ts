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
    // the chip (/), the instruction set (/isa/), the layout (/silicon/), the hardware bench (/fpga/)
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        isa: resolve(__dirname, "isa/index.html"),
        silicon: resolve(__dirname, "silicon/index.html"),
        fpga: resolve(__dirname, "fpga/index.html"),
      },
    },
  },
});
