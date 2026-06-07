import { useCallback, useEffect, useRef, useState } from "react";

export interface SubframeBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

const SMALLFACE_W = 48;
const SMALLFACE_H = 43;
const ZOOM = 10;

const VANILLA_EYE: SubframeBox = { x: 10, y: 8, w: 17, h: 6 };
const VANILLA_MOUTH: SubframeBox = { x: 7, y: 28, w: 14, h: 6 };
const VENGEANCE_EYE: SubframeBox = { x: 8, y: 6, w: 31, h: 13 };
const VENGEANCE_MOUTH: SubframeBox = { x: 8, y: 22, w: 32, h: 21 };

type DrawTarget = "eye" | "mouth" | null;

interface Props {
  /** PNG/JPG File the user uploaded as the base portrait. */
  portrait: File | null;
  eyeBox: SubframeBox;
  mouthBox: SubframeBox;
  onEyeBoxChange: (box: SubframeBox) => void;
  onMouthBoxChange: (box: SubframeBox) => void;
}

function clampBox(b: SubframeBox): SubframeBox {
  const x = Math.max(0, Math.min(SMALLFACE_W - 1, Math.floor(b.x)));
  const y = Math.max(0, Math.min(SMALLFACE_H - 1, Math.floor(b.y)));
  const w = Math.max(1, Math.min(SMALLFACE_W - x, Math.floor(b.w)));
  const h = Math.max(1, Math.min(SMALLFACE_H - y, Math.floor(b.h)));
  return { x, y, w, h };
}

/** Snap a drag-resolved (w, h) to the nearest preset size when within
 * `tolerance` pixels of one. Holding Shift bypasses the snap so a
 * power user can place freeform sizes. */
function snapSizeToPreset(
  w: number,
  h: number,
  target: "eye" | "mouth",
  tolerance: number = 2,
): { w: number; h: number; snapped: boolean } {
  const candidates = target === "eye"
    ? [{ w: VANILLA_EYE.w, h: VANILLA_EYE.h }, { w: VENGEANCE_EYE.w, h: VENGEANCE_EYE.h }]
    : [{ w: VANILLA_MOUTH.w, h: VANILLA_MOUTH.h }, { w: VENGEANCE_MOUTH.w, h: VENGEANCE_MOUTH.h }];
  for (const c of candidates) {
    if (Math.abs(w - c.w) <= tolerance && Math.abs(h - c.h) <= tolerance) {
      return { w: c.w, h: c.h, snapped: true };
    }
  }
  return { w, h, snapped: false };
}

export default function EyeMouthPicker({
  portrait,
  eyeBox,
  mouthBox,
  onEyeBoxChange,
  onMouthBoxChange,
}: Props) {
  const [previewDataUrl, setPreviewDataUrl] = useState<string | null>(null);
  const [drawTarget, setDrawTarget] = useState<DrawTarget>(null);
  const [dragStart, setDragStart] = useState<{ x: number; y: number } | null>(null);
  const [dragCurrent, setDragCurrent] = useState<{ x: number; y: number } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    if (!portrait) {
      setPreviewDataUrl(null);
      return;
    }
    const url = URL.createObjectURL(portrait);
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = SMALLFACE_W;
      canvas.height = SMALLFACE_H;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const sw = img.naturalWidth;
      const sh = img.naturalHeight;
      const targetAspect = SMALLFACE_W / SMALLFACE_H;
      const srcAspect = sw / sh;
      let sx = 0, sy = 0, scw = sw, sch = sh;
      if (srcAspect > targetAspect) {
        scw = Math.round(sh * targetAspect);
        sx = Math.round((sw - scw) / 2);
      } else if (srcAspect < targetAspect) {
        sch = Math.round(sw / targetAspect);
        sy = Math.round((sh - sch) / 2);
      }
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(img, sx, sy, scw, sch, 0, 0, SMALLFACE_W, SMALLFACE_H);
      setPreviewDataUrl(canvas.toDataURL("image/png"));
      URL.revokeObjectURL(url);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      setPreviewDataUrl(null);
    };
    img.src = url;
  }, [portrait]);

  const eventToLogical = useCallback((e: React.MouseEvent): { x: number; y: number } => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const rect = svg.getBoundingClientRect();
    return {
      x: Math.floor((e.clientX - rect.left) / ZOOM),
      y: Math.floor((e.clientY - rect.top) / ZOOM),
    };
  }, []);

  const handleMouseDown = (e: React.MouseEvent) => {
    if (!drawTarget) return;
    e.preventDefault();
    const p = eventToLogical(e);
    setDragStart(p);
    setDragCurrent(p);
  };
  const handleMouseMove = (e: React.MouseEvent) => {
    if (!dragStart) return;
    setDragCurrent(eventToLogical(e));
  };
  const handleMouseUp = (e: React.MouseEvent) => {
    if (!dragStart || !dragCurrent || !drawTarget) {
      setDragStart(null);
      setDragCurrent(null);
      return;
    }
    const x = Math.min(dragStart.x, dragCurrent.x);
    const y = Math.min(dragStart.y, dragCurrent.y);
    let w = Math.max(1, Math.abs(dragCurrent.x - dragStart.x) + 1);
    let h = Math.max(1, Math.abs(dragCurrent.y - dragStart.y) + 1);
    // Magnetic snap to Vanilla / Big Frames preset sizes when close.
    // Hold Shift on mouse-up to bypass and keep the freeform size.
    if (!e.shiftKey) {
      const snap = snapSizeToPreset(w, h, drawTarget);
      w = snap.w;
      h = snap.h;
    }
    const box = clampBox({ x, y, w, h });
    if (drawTarget === "eye") onEyeBoxChange(box);
    else onMouthBoxChange(box);
    setDragStart(null);
    setDragCurrent(null);
  };

  const dragRect = dragStart && dragCurrent
    ? clampBox({
        x: Math.min(dragStart.x, dragCurrent.x),
        y: Math.min(dragStart.y, dragCurrent.y),
        w: Math.max(1, Math.abs(dragCurrent.x - dragStart.x) + 1),
        h: Math.max(1, Math.abs(dragCurrent.y - dragStart.y) + 1),
      })
    : null;

  const updateBox = (target: "eye" | "mouth", field: keyof SubframeBox, value: number) => {
    const current = target === "eye" ? eyeBox : mouthBox;
    const next = clampBox({ ...current, [field]: value });
    if (target === "eye") onEyeBoxChange(next);
    else onMouthBoxChange(next);
  };

  return (
    <div className="space-y-3">
      {!portrait ? (
        <div className="border border-dashed border-wasteland-700 rounded p-6 text-center text-wasteland-500 text-sm">
          Upload a portrait above first — the picker shows it at {ZOOM}× zoom so you can drag rectangles
          around the eye and mouth regions.
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-wasteland-300">Draw region:</span>
            <button
              type="button"
              className={`px-2 py-1 rounded border transition-colors ${
                drawTarget === "eye"
                  ? "border-blue-400 bg-blue-500/20 text-blue-200"
                  : "border-wasteland-700 text-wasteland-300 hover:border-wasteland-500"
              }`}
              onClick={() => setDrawTarget(drawTarget === "eye" ? null : "eye")}
            >
              Eye region
            </button>
            <button
              type="button"
              className={`px-2 py-1 rounded border transition-colors ${
                drawTarget === "mouth"
                  ? "border-orange-400 bg-orange-500/20 text-orange-200"
                  : "border-wasteland-700 text-wasteland-300 hover:border-wasteland-500"
              }`}
              onClick={() => setDrawTarget(drawTarget === "mouth" ? null : "mouth")}
            >
              Mouth region
            </button>
            <span className="text-wasteland-600">·</span>
            <button
              type="button"
              className="px-2 py-1 rounded border border-wasteland-700 text-wasteland-300 hover:border-wasteland-500"
              onClick={() => {
                onEyeBoxChange(VANILLA_EYE);
                onMouthBoxChange(VANILLA_MOUTH);
              }}
              title="Reset to vanilla 1.13 defaults"
            >
              Vanilla
            </button>
            <button
              type="button"
              className="px-2 py-1 rounded border border-wasteland-700 text-wasteland-300 hover:border-wasteland-500"
              onClick={() => {
                onEyeBoxChange(VENGEANCE_EYE);
                onMouthBoxChange(VENGEANCE_MOUTH);
              }}
              title="Larger sub-frames (like Vengeance Reloaded)"
            >
              Big Frames
            </button>
          </div>

          <div
            className="inline-block relative bg-wasteland-900 rounded border border-wasteland-700 overflow-hidden"
            style={{ width: SMALLFACE_W * ZOOM, height: SMALLFACE_H * ZOOM }}
          >
            {previewDataUrl && (
              <img
                src={previewDataUrl}
                alt="SmallFace preview"
                width={SMALLFACE_W * ZOOM}
                height={SMALLFACE_H * ZOOM}
                style={{ imageRendering: "pixelated", display: "block" }}
              />
            )}
            <svg
              ref={svgRef}
              className={`absolute inset-0 ${drawTarget ? "cursor-crosshair" : "cursor-default"}`}
              width={SMALLFACE_W * ZOOM}
              height={SMALLFACE_H * ZOOM}
              onMouseDown={handleMouseDown}
              onMouseMove={handleMouseMove}
              onMouseUp={handleMouseUp}
              onMouseLeave={handleMouseUp}
            >
              <rect
                x={eyeBox.x * ZOOM}
                y={eyeBox.y * ZOOM}
                width={eyeBox.w * ZOOM}
                height={eyeBox.h * ZOOM}
                fill="rgba(59, 130, 246, 0.15)"
                stroke="rgb(96, 165, 250)"
                strokeWidth={2}
              />
              <rect
                x={mouthBox.x * ZOOM}
                y={mouthBox.y * ZOOM}
                width={mouthBox.w * ZOOM}
                height={mouthBox.h * ZOOM}
                fill="rgba(249, 115, 22, 0.15)"
                stroke="rgb(251, 146, 60)"
                strokeWidth={2}
              />
              {dragRect && (
                <rect
                  x={dragRect.x * ZOOM}
                  y={dragRect.y * ZOOM}
                  width={dragRect.w * ZOOM}
                  height={dragRect.h * ZOOM}
                  fill={drawTarget === "eye" ? "rgba(59, 130, 246, 0.35)" : "rgba(249, 115, 22, 0.35)"}
                  stroke={drawTarget === "eye" ? "rgb(147, 197, 253)" : "rgb(253, 186, 116)"}
                  strokeWidth={2}
                  strokeDasharray="4 2"
                />
              )}
            </svg>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3 text-xs max-w-2xl">
            {(["eye", "mouth"] as const).map((target) => {
              const box = target === "eye" ? eyeBox : mouthBox;
              const colorClass = target === "eye" ? "text-blue-300" : "text-orange-300";
              // Tailwind's `accent-*` utility colors the native range
              // thumb + track. Cleanest cross-browser way to get a
              // blue/orange differentiation without writing -webkit-
              // slider CSS by hand.
              const accentClass = target === "eye"
                ? "accent-blue-400"
                : "accent-orange-400";
              return (
                <div key={target} className="space-y-1.5">
                  <div className={`font-semibold ${colorClass}`}>
                    {target === "eye" ? "Eye" : "Mouth"} region
                  </div>
                  {(["x", "y", "w", "h"] as const).map((field) => {
                    const isHorizontal = field === "x" || field === "w";
                    const min = field === "x" || field === "y" ? 0 : 1;
                    const max = isHorizontal ? SMALLFACE_W : SMALLFACE_H;
                    const value = box[field];
                    return (
                      <div key={field} className="flex items-center gap-2">
                        <span className="text-wasteland-500 w-3 font-mono">{field}</span>
                        <input
                          type="range"
                          min={min}
                          max={max}
                          step={1}
                          value={value}
                          onChange={(e) => updateBox(target, field, Number(e.target.value) || 0)}
                          className={`flex-1 ${accentClass}`}
                          title={`${target} ${field}: ${value} (range ${min}–${max})`}
                        />
                        <span className="font-mono text-[10px] text-wasteland-200 w-7 text-right">
                          {value}
                        </span>
                        {/* Step ± buttons next to the slider give
                            keyboard-free precise nudging. The slider
                            itself is fine for big moves; the buttons
                            are for the "I need this one pixel up"
                            case. */}
                        <button
                          type="button"
                          onClick={() => updateBox(target, field, value - 1)}
                          disabled={value <= min}
                          title={`${field} − 1`}
                          className="rounded border border-wasteland-700 bg-wasteland-900 px-1.5 text-wasteland-300 hover:border-wasteland-500 disabled:opacity-40"
                        >
                          −
                        </button>
                        <button
                          type="button"
                          onClick={() => updateBox(target, field, value + 1)}
                          disabled={value >= max}
                          title={`${field} + 1`}
                          className="rounded border border-wasteland-700 bg-wasteland-900 px-1.5 text-wasteland-300 hover:border-wasteland-500 disabled:opacity-40"
                        >
                          +
                        </button>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>

          <p className="text-xs text-wasteland-500">
            The rectangle's position (x, y) sets where the eye and mouth animation appears on the
            48×43 portrait. Its width and height set the size of the animation frames. Vanilla mercs
            use 17×6 eyes / 14×6 mouths; the "Big Frames" preset uses 31×13 / 32×21. Any size that
            fits inside the 48×43 portrait works.
          </p>
          <p className="text-[10px] text-wasteland-500">
            Tip: drag-rectangles snap to Vanilla / Big Frames preset
            sizes when they're within 2 pixels of a match. Hold{" "}
            <kbd className="font-mono bg-wasteland-900 px-1 rounded">Shift</kbd> while
            releasing the mouse to keep a freeform size.
          </p>
        </>
      )}
    </div>
  );
}
