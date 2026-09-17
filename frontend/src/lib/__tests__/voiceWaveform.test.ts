import { describe, expect, it } from "vitest";

import {
  adjustCutBoundary,
  completeWaveformGesture,
  timeToPercent,
} from "../voiceWaveform";

describe("waveform gestures", () => {
  it("turns pointer release after a drag into a normalized persistent cut", () => {
    expect(completeWaveformGesture(80, 20, 100, 2000)).toEqual({
      kind: "cut",
      range: { startMs: 400, endMs: 1600 },
    });
  });

  it("turns a click into a seek without creating a zero-length cut", () => {
    expect(completeWaveformGesture(25, 26, 100, 2000)).toEqual({ kind: "seek", timeMs: 500 });
  });

  it("maps playback time and keeps adjusted handles in valid bounds", () => {
    expect(timeToPercent(750, 3000)).toBe(25);
    expect(adjustCutBoundary({ startMs: 500, endMs: 1000 }, "start", 1200, 3000))
      .toEqual({ startMs: 999, endMs: 1000 });
    expect(adjustCutBoundary({ startMs: 500, endMs: 1000 }, "end", -10, 3000))
      .toEqual({ startMs: 500, endMs: 501 });
  });
});
