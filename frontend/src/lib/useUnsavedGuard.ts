import { useCallback, useEffect } from "react";
import { useNavigate } from "react-router-dom";

const CONFIRM_TEXT =
  "You have unsaved changes. Leave anyway and discard them?\n\n"
  + "OK = discard + go. Cancel = stay so you can Save first.";

/**
 * Guard against silent loss of in-progress form work when the user
 * navigates away. Three layers:
 *
 *   1. `beforeunload` listener — catches Tauri-window-close, browser
 *      refresh, and the OS sending us a quit signal. Modern browsers
 *      only show a generic "Changes you made may not be saved" dialog
 *      regardless of `returnValue`; we set it anyway so the prompt
 *      fires.
 *   2. popstate sentinel — catches the BACK button (incl. mouse button
 *      4, which the Tauri webview honors). While dirty, a same-URL
 *      sentinel entry sits on top of the history stack; back pops the
 *      sentinel (staying on this page), we confirm, and either proceed
 *      (another back) or re-arm. This exists because React Router 6's
 *      `useBlocker` requires the data-router setup, which destabilized
 *      when tried — the sentinel gives the same protection on the plain
 *      <BrowserRouter>. Known wart: a stale sentinel can leave one
 *      duplicate history entry after in-app navigation — a no-op extra
 *      back-press, not data loss.
 *   3. `confirmNavigate(to)` — wraps `useNavigate` with a confirm
 *      check. Use this on any Link click handler / button click that
 *      leaves the page. (Plain un-wrapped <Link>s still bypass the
 *      guard — wrap them.)
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

  useEffect(() => {
    if (!dirty) return;
    // Arm: push a same-URL sentinel so the first back-press pops the
    // sentinel (staying on this page) instead of leaving — that's our
    // window to confirm. Guard against double-arming on dirty toggles.
    if (!(window.history.state as { __unsavedGuard?: boolean } | null)
        ?.__unsavedGuard) {
      window.history.pushState({ __unsavedGuard: true }, "");
    }
    const onPop = () => {
      if (window.confirm(CONFIRM_TEXT)) {
        // Proceed with the back the user asked for (past the entry we
        // just popped onto).
        window.removeEventListener("popstate", onPop);
        window.history.back();
      } else {
        // Re-arm for the next back-press.
        window.history.pushState({ __unsavedGuard: true }, "");
      }
    };
    window.addEventListener("popstate", onPop);
    return () => {
      window.removeEventListener("popstate", onPop);
      // Disarm: if the sentinel is still the top entry (dirty flipped
      // false, e.g. after a successful save), pop it so Back isn't a
      // visible no-op. State-guarded so an unmount after real navigation
      // (top entry is the new route's) doesn't rewind the router. Edge:
      // history.back() is async — a save that re-dirties in the same
      // tick can re-arm before the pop lands, costing one unguarded
      // back-press; accepted as rare.
      if ((window.history.state as { __unsavedGuard?: boolean } | null)
          ?.__unsavedGuard) {
        window.history.back();
      }
    };
  }, [dirty]);

  const confirmNavigate = useCallback(
    (to: string) => {
      if (dirty) {
        const ok = window.confirm(CONFIRM_TEXT);
        if (!ok) return;
      }
      navigate(to);
    },
    [dirty, navigate],
  );

  return { confirmNavigate };
}
