import { afterEach, describe, expect, it, vi } from "vitest";

import { pollVoiceLabJob, pollVoiceLabJobs, reconcileVoiceLabScanJob } from "../voiceLabPolling";
import type { VoiceLabJob } from "../voiceLabSchema";

const runningJob: VoiceLabJob = {
  jobId: "scan-1",
  kind: "scan",
  status: "running",
  completed: 1,
  total: 2,
  lastMessage: "scanning",
  cancelRequested: false,
};

const completedJob: VoiceLabJob = { ...runningJob, status: "complete", completed: 2, lastMessage: "complete" };

afterEach(() => vi.useRealTimers());

describe("pollVoiceLabJob", () => {
  it("continues polling one second after a transient fetch failure and stops when complete", async () => {
    vi.useFakeTimers();
    const getJob = vi.fn<() => Promise<VoiceLabJob>>()
      .mockRejectedValueOnce(new Error("temporary sidecar disconnect"))
      .mockResolvedValueOnce(runningJob)
      .mockResolvedValueOnce(completedJob);
    const onError = vi.fn();
    const onComplete = vi.fn();

    const stop = pollVoiceLabJob({ job: runningJob, getJob, onJob: vi.fn(), onError, onComplete });

    await vi.advanceTimersByTimeAsync(1_000);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(getJob).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1_000);
    expect(getJob).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(1_000);
    expect(getJob).toHaveBeenCalledTimes(3);
    expect(onComplete).toHaveBeenCalledWith(completedJob);

    await vi.advanceTimersByTimeAsync(5_000);
    expect(getJob).toHaveBeenCalledTimes(3);
    stop();
  });

  it("clears its scheduled poll when disposed", async () => {
    vi.useFakeTimers();
    const getJob = vi.fn<() => Promise<VoiceLabJob>>().mockResolvedValue(runningJob);
    const stop = pollVoiceLabJob({ job: runningJob, getJob, onJob: vi.fn(), onError: vi.fn(), onComplete: vi.fn() });

    stop();
    await vi.advanceTimersByTimeAsync(1_000);

    expect(getJob).not.toHaveBeenCalled();
  });
});

describe("pollVoiceLabJobs", () => {
  it("polls every active scan independently so one completion cannot orphan another", async () => {
    vi.useFakeTimers();
    const secondRunning = { ...runningJob, jobId: "scan-2", completed: 0 };
    const secondCompleted: VoiceLabJob = { ...secondRunning, status: "complete", completed: 2, lastMessage: "complete" };
    let secondPolls = 0;
    const getJob = vi.fn<(jobId: string) => Promise<VoiceLabJob>>()
      .mockImplementation(async (jobId) => {
        if (jobId === "scan-1") return completedJob;
        secondPolls += 1;
        return secondPolls === 1 ? secondRunning : secondCompleted;
      });
    const onComplete = vi.fn();

    const stop = pollVoiceLabJobs({
      jobs: [runningJob, secondRunning],
      getJob,
      onJob: vi.fn(),
      onError: vi.fn(),
      onComplete,
    });

    await vi.advanceTimersByTimeAsync(1_000);

    expect(getJob).toHaveBeenCalledTimes(2);
    expect(new Set(getJob.mock.calls.map(([jobId]) => jobId))).toEqual(new Set(["scan-1", "scan-2"]));
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onComplete).toHaveBeenCalledWith(completedJob);

    await vi.advanceTimersByTimeAsync(1_000);

    expect(getJob).toHaveBeenCalledTimes(3);
    expect(onComplete).toHaveBeenCalledTimes(2);
    expect(onComplete).toHaveBeenCalledWith(secondCompleted);
    stop();
  });
});

describe("reconcileVoiceLabScanJob", () => {
  it.each(["complete", "failed", "cancelled"] as const)(
    "sends a %s cancel-race response through terminal cleanup",
    (status) => {
      const onActive = vi.fn();
      const onTerminal = vi.fn();
      const terminal = { ...runningJob, status };

      reconcileVoiceLabScanJob(terminal, { onActive, onTerminal });

      expect(onActive).not.toHaveBeenCalled();
      expect(onTerminal).toHaveBeenCalledWith(terminal);
    },
  );
});
