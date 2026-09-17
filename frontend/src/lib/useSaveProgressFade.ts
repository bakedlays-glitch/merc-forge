import { useEffect, useRef, useState } from "react";

import type { SaveProgressEvent } from "./api";

/**
 * Streaming-save progress state shared by the Move and Duplicate flows:
 * collect NDJSON progress events during the mutation, mark done on
 * success/error, and fade the bar out 2.5s after success. The fade
 * timeout is tracked in a ref and cancelled on unmount or when a fresh
 * mutation starts — pre-#113 the timeout fired unconditionally and React
 * warned about setState on an unmounted component. On error the bar
 * stays visible so the user can see which step failed.
 */
export function useSaveProgressFade() {
  const [events, setEvents] = useState<SaveProgressEvent[] | null>(null);
  const [done, setDone] = useState(false);
  const fadeTimeoutRef = useRef<number | null>(null);

  const cancelFade = () => {
    if (fadeTimeoutRef.current !== null) {
      window.clearTimeout(fadeTimeoutRef.current);
      fadeTimeoutRef.current = null;
    }
  };

  useEffect(() => cancelFade, []);

  return {
    events,
    done,
    /** Call at the top of mutationFn: clears any pending fade + resets state. */
    begin() {
      cancelFade();
      setEvents([]);
      setDone(false);
    },
    /** Append one streamed event. */
    push(ev: SaveProgressEvent) {
      setEvents((prev) => (prev ? [...prev, ev] : [ev]));
    },
    /** Call in onSuccess: shows the green ✓ then fades the bar out. */
    succeed() {
      setDone(true);
      fadeTimeoutRef.current = window.setTimeout(() => {
        setEvents(null);
        setDone(false);
        fadeTimeoutRef.current = null;
      }, 2500);
    },
    /** Call in onError: keep the bar visible so the failed step shows. */
    fail() {
      setDone(true);
    },
  };
}
