import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import App from "./App";
import "./styles/globals.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Sidecar is local — no need for aggressive caching
      staleTime: 30_000,
      // Retry up to 3 times on transient failures (network blip, sidecar
      // mid-respawn) with exponential backoff so a brief watchdog respawn
      // doesn't surface as an error to the user.
      retry: (failureCount, error) => {
        if (error && typeof error === "object" && "status" in error) {
          const status = (error as { status: number }).status;
          if (status >= 400 && status < 500) return false;
        }
        return failureCount < 3;
      },
      retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 8000),
    },
  },
});

// Roster portrait-sheet blobs: each cached query entry owns an object
// URL. Revoke it exactly when that entry LEAVES the cache (gcTime expiry,
// invalidation, or reset) — never on component unmount. The roster query
// holds the sheet with `staleTime: Infinity`, so the old unmount-time
// revoke freed a URL that was still cached and re-served on a quick
// return visit, blanking every portrait. Centralising revocation on the
// cache 'removed' event keeps the blob alive as long as the entry is, and
// covers both the route-mounted fetch and the startup prefetch.
queryClient.getQueryCache().subscribe((event) => {
  if (event.type !== "removed") return;
  const key = event.query.queryKey;
  if (Array.isArray(key) && key[0] === "roster-portrait-sheet") {
    const data = event.query.state.data as { blobUrl?: string } | undefined;
    if (data?.blobUrl) URL.revokeObjectURL(data.blobUrl);
  }
});

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("Root element not found");

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
