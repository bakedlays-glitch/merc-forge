import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri expects a fixed port for the dev server
const TAURI_DEV_PORT = 1420;

// ISO timestamp captured at the moment Vite reads this config — runs once
// per build. Surfaced into the bundle via `define` below so the running
// app can show "Built at 2026-05-23T20:47:18Z" on the Settings page.
// Lets the user tell at a glance whether the running shell matches their
// most recent `launch_current.ps1` rebuild. It addresses
// the recurring "I edited source but the app shows old text" confusion.
const BUILD_TIMESTAMP = new Date().toISOString();

export default defineConfig(async () => ({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: TAURI_DEV_PORT,
    strictPort: true,
    watch: {
      // Ignore the sidecar build output so we don't reload on test runs
      ignored: ["**/sidecar/.venv/**", "**/sidecar/dist/**"],
    },
  },
  envPrefix: ["VITE_", "TAURI_"],
  define: {
    __BUILD_TIMESTAMP__: JSON.stringify(BUILD_TIMESTAMP),
  },
  build: {
    target: "es2021",
    minify: !process.env.TAURI_DEBUG ? "esbuild" : false,
    sourcemap: !!process.env.TAURI_DEBUG,
    // The one chunk over vite's 500 kB default is MapForgeSector (~550 kB),
    // already route-lazy and served from local disk inside the Tauri shell,
    // so the web-oriented warning is noise here. Raise the limit just past
    // it — if a future chunk trips this again, that's a real signal, and
    // the durable fix is splitting the MapForgeSector monolith (tracked as
    // a do-when-touching refactor), not raising this further.
    chunkSizeWarningLimit: 600,
  },
}));
