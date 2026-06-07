/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SIDECAR_PORT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// Injected by vite.config.ts at build time. ISO 8601 timestamp captured
// when Vite reads the config — survives into the bundled JS as a literal
// string so the Settings page can display when the running app was built.
declare const __BUILD_TIMESTAMP__: string;
