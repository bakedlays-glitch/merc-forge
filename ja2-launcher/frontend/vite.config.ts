import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// JA2 Launcher uses port 1421 (MercForge frontend already owns 1420)
const TAURI_DEV_PORT = 1421;

const BUILD_TIMESTAMP = new Date().toISOString();

export default defineConfig(async () => ({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: TAURI_DEV_PORT,
    strictPort: true,
  },
  envPrefix: ["VITE_", "TAURI_"],
  define: {
    __BUILD_TIMESTAMP__: JSON.stringify(BUILD_TIMESTAMP),
  },
  build: {
    target: "es2021",
    minify: !process.env.TAURI_DEBUG ? "esbuild" : false,
    sourcemap: !!process.env.TAURI_DEBUG,
  },
}));
