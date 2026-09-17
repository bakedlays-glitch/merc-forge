import { describe, it, expect, vi } from "vitest";
import { trailingDebounce } from "../placementApi";

// oracleToVerdicts + mergeVerdicts coverage lives in mapPlacement.test.ts
// (mergeVerdicts was already fully covered there; oracleToVerdicts joined
// it rather than getting a second, duplicate suite here).
describe("trailingDebounce", () => {
  it("fires once with the last args after the window", () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const d = trailingDebounce(80, fn);
    d.call(1); d.call(2); d.call(3);
    vi.advanceTimersByTime(79); expect(fn).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1); expect(fn).toHaveBeenCalledOnce(); expect(fn).toHaveBeenCalledWith(3);
    d.call(4); d.cancel(); vi.advanceTimersByTime(100); expect(fn).toHaveBeenCalledOnce();
    vi.useRealTimers();
  });
});
