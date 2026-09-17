import { describe, expect, it, vi } from "vitest";

import { refreshVoiceLabAfterMutation } from "../voiceLabPolling";
import type { VoiceLabJob } from "../voiceLabSchema";

const running: VoiceLabJob = {
  jobId: "refresh-1", kind: "scan", status: "running", completed: 0, total: 2,
  lastMessage: "scanning", cancelRequested: false,
};

describe("refreshVoiceLabAfterMutation", () => {
  it("waits for a replacement scan before invalidating cached inventory", async () => {
    const complete = { ...running, status: "complete" as const, completed: 2, lastMessage: "complete" };
    const startScan = vi.fn().mockResolvedValue(running);
    const getJob = vi.fn().mockResolvedValueOnce(running).mockResolvedValueOnce(complete);
    const wait = vi.fn().mockResolvedValue(undefined);
    const invalidate = vi.fn().mockResolvedValue(undefined);

    await expect(refreshVoiceLabAfterMutation({ startScan, getJob, wait, invalidate })).resolves.toEqual(complete);

    expect(startScan).toHaveBeenCalledOnce();
    expect(getJob).toHaveBeenNthCalledWith(1, "refresh-1");
    expect(getJob).toHaveBeenNthCalledWith(2, "refresh-1");
    expect(invalidate).toHaveBeenCalledOnce();
  });

  it("surfaces a failed refresh without retrying the completed mutation", async () => {
    const failed = { ...running, status: "failed" as const, lastMessage: "scan failed" };
    const invalidate = vi.fn();

    await expect(refreshVoiceLabAfterMutation({
      startScan: vi.fn().mockResolvedValue(failed), getJob: vi.fn(), invalidate,
    })).rejects.toThrow("scan failed");

    expect(invalidate).not.toHaveBeenCalled();
  });
});
