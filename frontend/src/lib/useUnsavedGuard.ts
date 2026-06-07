import { useCallback, useEffect } from "react";
import { useNavigate } from "react-router-dom";

/**
 * Guard against silent loss of in-progress form work when the user
 * navigates away. Two layers:
 *
 *   1. `beforeunload` listener — catches Tauri-window-close, browser
 *      refresh, and the OS sending us a quit signal. Modern browsers
 *      only show a generic "Changes you made may not be saved" dialog
 *      regardless of `returnValue`; we set it anyway so the prompt
 *      fires.
 *   2. `confirmNavigate(to)` — wraps `useNavigate` with a `window.confirm`
 *      check. Use this on any Link click handler / button click that
 *      leaves the page. Matches the MapForgeSector pattern (see the
 *      "Pick a different merc" + "← Map Forge" handlers there). We
 *      deliberately do NOT use React Router 6's `useBlocker` — the
 *      project's RR6 version had stability issues with the data
 *      router setup last time we tried.
 */
export function useUnsavedGuard(dirty: boolean) {
  const navigate = useNavigate();

  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      // Spec: setting returnValue triggers the browser's generic
      // unsaved-changes prompt. The text we set is ignored by Chromium
      // for security reasons but must be non-empty.
      e.preventDefault();
      e.returnValue = "Unsaved changes — are you sure you want to leave?";
      return e.returnValue;
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  const confirmNavigate = useCallback(
    (to: string) => {
      if (dirty) {
        const ok = window.confirm(
          "You have unsaved changes. Leave anyway and discard them?\n\n"
          + "OK = discard + go. Cancel = stay so you can Save first."
        );
        if (!ok) return;
      }
      navigate(to);
    },
    [dirty, navigate],
  );

  return { confirmNavigate };
}
