import type { TimeRange } from "./voiceLabSchema";

export type WaveformGesture =
  | { kind: "seek"; timeMs: number }
  | { kind: "cut"; range: TimeRange };

function positionToTime(position: number, width: number, durationMs: number): number {
  const ratio = Math.max(0, Math.min(1, position / Math.max(1, width)));
  return Math.round(ratio * Math.max(0, durationMs));
}

export function completeWaveformGesture(
  startPosition: number,
  endPosition: number,
  width: number,
  durationMs: number,
  dragThreshold = 3,
): WaveformGesture {
  if (Math.abs(endPosition - startPosition) < dragThreshold) {
    return { kind: "seek", timeMs: positionToTime(startPosition, width, durationMs) };
  }
  const first = positionToTime(startPosition, width, durationMs);
  const second = positionToTime(endPosition, width, durationMs);
  return { kind: "cut", range: { startMs: Math.min(first, second), endMs: Math.max(first, second) } };
}

export function timeToPercent(timeMs: number, durationMs: number): number {
  if (durationMs <= 0) return 0;
  return Math.max(0, Math.min(100, timeMs / durationMs * 100));
}

export function adjustCutBoundary(
  cut: TimeRange,
  boundary: "start" | "end",
  valueMs: number,
  durationMs: number,
): TimeRange {
  if (boundary === "start") {
    return { startMs: Math.max(0, Math.min(cut.endMs - 1, valueMs)), endMs: cut.endMs };
  }
  return { startMs: cut.startMs, endMs: Math.min(durationMs, Math.max(cut.startMs + 1, valueMs)) };
}
