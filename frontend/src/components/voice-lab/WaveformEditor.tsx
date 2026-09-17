import { useEffect, useRef, useState } from "react";

import type { TimeRange } from "../../lib/voiceLabSchema";
import { insertNormalizedCut } from "../../lib/voiceLabViewModel";
import { adjustCutBoundary, completeWaveformGesture } from "../../lib/voiceWaveform";

interface Props {
  peaks: readonly number[];
  durationMs: number;
  cuts: readonly TimeRange[];
  currentTimeMs: number;
  onCutsChange: (cuts: TimeRange[]) => void;
  onSeek: (timeMs: number) => void;
  onSelectionChange?: (range: TimeRange | null) => void;
}

function seconds(milliseconds: number): string {
  return `${(milliseconds / 1000).toFixed(2)}s`;
}

export default function WaveformEditor({ peaks, durationMs, cuts, currentTimeMs, onCutsChange, onSeek, onSelectionChange }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dragStart = useRef<number | null>(null);
  const suppressClick = useRef(false);
  const [candidate, setCandidate] = useState<TimeRange | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const selectedRange = selected === null ? candidate : cuts[selected] ?? null;

  useEffect(() => { onSelectionChange?.(selectedRange); }, [onSelectionChange, selectedRange?.startMs, selectedRange?.endMs]);
  useEffect(() => {
    const canvas = canvasRef.current; const context = canvas?.getContext("2d");
    if (!canvas || !context) return;
    const width = Math.max(1, Math.round(canvas.clientWidth)); canvas.width = width; canvas.height = 150;
    context.clearRect(0, 0, width, 150); context.fillStyle = "#111518"; context.fillRect(0, 0, width, 150);
    const band = (range: TimeRange, color: string) => { const x = range.startMs / durationMs * width; const w = (range.endMs - range.startMs) / durationMs * width; context.fillStyle = color; context.fillRect(x, 0, Math.max(1, w), 150); };
    cuts.forEach((cut, index) => band(cut, index === selected ? "rgba(224, 112, 58, .62)" : "rgba(207, 99, 61, .32)"));
    if (candidate) band(candidate, "rgba(240, 180, 90, .48)");
    context.strokeStyle = "#8d988f"; context.lineWidth = 1.5; context.beginPath();
    peaks.forEach((peak, index) => { const x = index / Math.max(1, peaks.length - 1) * width; const y = 75 - Math.max(-1, Math.min(1, peak)) * 58; index ? context.lineTo(x, y) : context.moveTo(x, y); }); context.stroke();
    const playheadX = Math.max(0, Math.min(1, currentTimeMs / Math.max(1, durationMs))) * width;
    context.strokeStyle = "#f2d7a1"; context.lineWidth = 2; context.beginPath(); context.moveTo(playheadX, 0); context.lineTo(playheadX, 150); context.stroke();
  }, [candidate, currentTimeMs, cuts, durationMs, peaks, selected]);

  function localX(event: React.PointerEvent<HTMLCanvasElement> | React.MouseEvent<HTMLCanvasElement>): number {
    const rect = event.currentTarget.getBoundingClientRect();
    const clientRelative = event.clientX - rect.left;
    const relative = clientRelative >= 0 && clientRelative <= rect.width
      ? clientRelative
      : event.nativeEvent.offsetX;
    return Math.max(0, Math.min(rect.width, relative));
  }
  function updateSelected(boundary: "start" | "end", value: number) {
    if (selected === null || !cuts[selected]) return;
    const next = cuts.map((cut, index) => index === selected ? adjustCutBoundary(cut, boundary, value, durationMs) : cut);
    onCutsChange(next);
  }
  function removeSelected() {
    if (selected === null) return;
    onCutsChange(cuts.filter((_, index) => index !== selected));
    setSelected(null);
  }

  return <div className="border border-wasteland-700 bg-wasteland-950 p-2">
    <div className="relative">
      <canvas
        ref={canvasRef}
        tabIndex={0}
        role="application"
        aria-label="Audio waveform. Click to seek. Drag to mark words to remove."
        title="Click to move playback. Drag across words you want to remove."
        className="h-36 w-full cursor-crosshair touch-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rust-400"
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          const start = localX(event); dragStart.current = start; setSelected(null);
          const time = Math.round(start / Math.max(1, event.currentTarget.getBoundingClientRect().width) * durationMs);
          setCandidate({ startMs: time, endMs: time });
        }}
        onPointerMove={(event) => {
          if (dragStart.current === null) return;
          const end = localX(event); const width = event.currentTarget.getBoundingClientRect().width;
          const gesture = completeWaveformGesture(dragStart.current, end, width, durationMs, 0);
          if (gesture.kind === "cut") setCandidate(gesture.range);
        }}
        onPointerUp={(event) => {
          if (dragStart.current === null) return;
          const start = dragStart.current; const end = localX(event); dragStart.current = null;
          const gesture = completeWaveformGesture(start, end, event.currentTarget.getBoundingClientRect().width, durationMs);
          suppressClick.current = true;
          if (gesture.kind === "seek") { setCandidate(null); setSelected(null); onSeek(gesture.timeMs); return; }
          try {
            const inserted = insertNormalizedCut(cuts, gesture.range, durationMs);
            onCutsChange(inserted.cuts); setSelected(inserted.selectedIndex); setCandidate(null);
          } catch { setCandidate(null); }
        }}
        onClick={(event) => {
          if (suppressClick.current) { suppressClick.current = false; return; }
          const timeMs = Math.round(localX(event) / Math.max(1, event.currentTarget.getBoundingClientRect().width) * durationMs);
          setCandidate(null); setSelected(null); onSeek(timeMs);
        }}
        onPointerCancel={() => { dragStart.current = null; setCandidate(null); }}
        onKeyDown={(event) => {
          if (event.key === "Escape") { setCandidate(null); setSelected(null); }
          if ((event.key === "Delete" || event.key === "Backspace") && selected !== null) { event.preventDefault(); removeSelected(); }
        }}
      />
      <span className="pointer-events-none absolute bottom-1 left-2 rounded-sm bg-wasteland-950/80 px-1.5 py-0.5 font-mono text-[10px] text-wasteland-300">{seconds(currentTimeMs)} / {seconds(durationMs)}</span>
    </div>
    <p className="mt-2 text-[11px] text-wasteland-400">Click to seek. Drag over words to remove them; the orange cut saves immediately.</p>
    {cuts.length > 0 && <div className="mt-3 space-y-2 border-t border-wasteland-800 pt-3">
      {cuts.map((cut, index) => <div key={`${index}-${cut.startMs}-${cut.endMs}`} className={`flex flex-wrap items-center gap-2 px-2 py-1.5 text-xs ${selected === index ? "bg-rust-500/15" : "bg-wasteland-900/60"}`}>
        <button type="button" title="Select this cut so you can adjust or remove it" className="min-w-20 text-left font-medium text-wasteland-100" onClick={() => setSelected(index)}>Cut {index + 1}</button>
        <label className="flex items-center gap-1 text-wasteland-400">Start <input title="Exact point where removed audio begins" aria-label={`Cut ${index + 1} start in milliseconds`} type="number" min={0} max={cut.endMs - 1} value={cut.startMs} onFocus={() => setSelected(index)} onChange={(event) => { setSelected(index); updateSelected("start", Number(event.target.value)); }} className="w-24 bg-wasteland-950 font-mono text-xs" /> ms</label>
        <label className="flex items-center gap-1 text-wasteland-400">End <input title="Exact point where removed audio ends" aria-label={`Cut ${index + 1} end in milliseconds`} type="number" min={cut.startMs + 1} max={durationMs} value={cut.endMs} onFocus={() => setSelected(index)} onChange={(event) => { setSelected(index); updateSelected("end", Number(event.target.value)); }} className="w-24 bg-wasteland-950 font-mono text-xs" /> ms</label>
        <button type="button" title="Keep the audio and delete this cut instruction" className="btn-ghost ml-auto px-2 py-1" onClick={() => { setSelected(index); onCutsChange(cuts.filter((_, item) => item !== index)); setSelected(null); }}>Remove</button>
      </div>)}
      <button type="button" title="Restore the most recently cut section" className="btn-ghost px-2 py-1 text-xs" onClick={() => { onCutsChange(cuts.slice(0, -1)); setSelected(null); }}>Undo last cut</button>
    </div>}
  </div>;
}
