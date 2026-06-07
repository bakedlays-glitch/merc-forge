/**
 * A reusable in-app floating panel: draggable by its title bar,
 * resizable from the bottom-right corner, clamped to the viewport, and
 * persists its position+size to localStorage (keyed by `id`).
 *
 * It is a plain `position: fixed` overlay — NOT a separate OS window —
 * so the content stays inside the same React tree and shares state with
 * the rest of the editor (no Tauri multi-window / IPC). z-index sits
 * below the asset-viewer modal (z-40) so opening that modal covers any
 * floating panels rather than fighting them.
 *
 * Open/closed state is owned by the PARENT (so the parent decides what
 * "docked" looks like when closed). This component only manages
 * geometry + drag/resize. `onClose` fires when the user clicks the
 * panel's ✕ ("dock back").
 *
 * Pointer capture (not window listeners) drives drag/resize, so a fast
 * drag that outruns the cursor still tracks correctly and there's no
 * listener add/remove bookkeeping to leak.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export interface PanelRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

const STORAGE_PREFIX = "mapforge.floatpanel.";

/** Keep a rect inside the viewport and above the min size. Also caps
 * w/h to the viewport so a panel restored on a smaller screen can't be
 * larger than the window. */
export function clampRect(r: PanelRect, minW: number, minH: number): PanelRect {
  const vw = typeof window !== "undefined" ? window.innerWidth : 1280;
  const vh = typeof window !== "undefined" ? window.innerHeight : 800;
  const w = Math.max(minW, Math.min(r.w, vw));
  const h = Math.max(minH, Math.min(r.h, vh));
  const x = Math.max(0, Math.min(r.x, vw - w));
  const y = Math.max(0, Math.min(r.y, vh - h));
  return { x, y, w, h };
}

function loadRect(
  id: string, fallback: PanelRect, minW: number, minH: number,
): PanelRect {
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + id);
    if (!raw) return clampRect(fallback, minW, minH);
    const p = JSON.parse(raw) as Partial<PanelRect>;
    return clampRect({
      x: typeof p.x === "number" ? p.x : fallback.x,
      y: typeof p.y === "number" ? p.y : fallback.y,
      w: typeof p.w === "number" ? p.w : fallback.w,
      h: typeof p.h === "number" ? p.h : fallback.h,
    }, minW, minH);
  } catch {
    return clampRect(fallback, minW, minH);
  }
}

interface DragState {
  mode: "move" | "resize";
  px: number;
  py: number;
  start: PanelRect;
}

export interface FloatingPanelProps {
  /** Stable id used as the localStorage persistence key. */
  id: string;
  title: string;
  /** Geometry used the first time this panel is opened (before the user
   * has dragged/resized it). Subsequent opens restore the saved rect. */
  defaultRect: PanelRect;
  minW?: number;
  minH?: number;
  /** Fires when the user clicks ✕ — the parent should re-dock (hide). */
  onClose: () => void;
  /** Optional controls rendered in the title bar (e.g. tile-size +/−).
   * Pointer events inside are stopped so interacting with them doesn't
   * start a window drag. */
  headerRight?: React.ReactNode;
  children: React.ReactNode;
}

export function FloatingPanel({
  id, title, defaultRect, minW = 180, minH = 140, onClose, headerRight, children,
}: FloatingPanelProps) {
  const [rect, setRect] = useState<PanelRect>(
    () => loadRect(id, defaultRect, minW, minH),
  );
  const rectRef = useRef(rect);
  const dragRef = useRef<DragState | null>(null);

  const apply = useCallback((r: PanelRect) => {
    const c = clampRect(r, minW, minH);
    rectRef.current = c;
    setRect(c);
  }, [minW, minH]);

  const persist = useCallback(() => {
    try {
      localStorage.setItem(STORAGE_PREFIX + id, JSON.stringify(rectRef.current));
    } catch {
      /* localStorage quota / disabled — geometry just won't persist */
    }
  }, [id]);

  // Re-clamp if the window shrinks so the panel never strands off-screen.
  useEffect(() => {
    function onResize() {
      apply(rectRef.current);
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [apply]);

  function beginDrag(mode: "move" | "resize", e: React.PointerEvent) {
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragRef.current = {
      mode, px: e.clientX, py: e.clientY, start: rectRef.current,
    };
  }

  function moveDrag(e: React.PointerEvent) {
    const d = dragRef.current;
    if (!d) return;
    const dx = e.clientX - d.px;
    const dy = e.clientY - d.py;
    if (d.mode === "move") {
      apply({ ...d.start, x: d.start.x + dx, y: d.start.y + dy });
    } else {
      apply({ ...d.start, w: d.start.w + dx, h: d.start.h + dy });
    }
  }

  function endDrag(e: React.PointerEvent) {
    if (!dragRef.current) return;
    dragRef.current = null;
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {
      /* pointer already released */
    }
    persist();
  }

  return (
    <div
      className="fixed z-30 flex flex-col overflow-hidden rounded-lg border border-gray-600 bg-gray-950 shadow-2xl"
      style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }}
      role="dialog"
      aria-label={title}
    >
      <div
        className="flex select-none items-center gap-2 border-b border-gray-700 bg-gray-900 px-2 py-1"
        style={{ cursor: "move", touchAction: "none" }}
        onPointerDown={(e) => beginDrag("move", e)}
        onPointerMove={moveDrag}
        onPointerUp={endDrag}
      >
        <span className="flex-1 truncate text-xs font-semibold text-gray-200">
          {title}
        </span>
        {headerRight != null && (
          <span
            className="flex items-center gap-1"
            onPointerDown={(e) => e.stopPropagation()}
          >
            {headerRight}
          </span>
        )}
        <button
          type="button"
          onClick={onClose}
          onPointerDown={(e) => e.stopPropagation()}
          title="Dock back into the side panel"
          className="rounded px-1 text-sm leading-none text-gray-400 hover:bg-gray-700 hover:text-gray-100"
        >
          ✕
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-2">
        {children}
      </div>

      <div
        onPointerDown={(e) => beginDrag("resize", e)}
        onPointerMove={moveDrag}
        onPointerUp={endDrag}
        title="Drag to resize"
        className="absolute bottom-0 right-0 h-4 w-4"
        style={{
          cursor: "nwse-resize",
          touchAction: "none",
          background: "linear-gradient(135deg, transparent 45%, rgb(107 114 128) 45%, rgb(107 114 128) 55%, transparent 55%, transparent 70%, rgb(107 114 128) 70%, rgb(107 114 128) 80%, transparent 80%)",
        }}
      />
    </div>
  );
}
