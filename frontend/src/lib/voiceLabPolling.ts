import type { VoiceLabJob } from "./voiceLabSchema";

export function isActiveVoiceLabJob(job: VoiceLabJob): boolean {
  return job.status === "queued" || job.status === "running" || job.status === "cancelling";
}

interface PollVoiceLabJobOptions {
  job: VoiceLabJob;
  getJob: (jobId: string) => Promise<VoiceLabJob>;
  onJob: (job: VoiceLabJob) => void;
  onError: (error: unknown) => void;
  onComplete: (job: VoiceLabJob) => void;
  intervalMs?: number;
}

interface PollVoiceLabJobsOptions {
  jobs: readonly VoiceLabJob[];
  getJob: (jobId: string) => Promise<VoiceLabJob>;
  onJob: (job: VoiceLabJob) => void;
  onError: (error: unknown, job: VoiceLabJob) => void;
  onComplete: (job: VoiceLabJob) => void;
  intervalMs?: number;
}

interface ReconcileVoiceLabScanJobOptions {
  onActive: (job: VoiceLabJob) => void;
  onTerminal: (job: VoiceLabJob) => void;
}

/** Route a scan response through exactly one lifecycle branch. */
export function reconcileVoiceLabScanJob(
  job: VoiceLabJob,
  { onActive, onTerminal }: ReconcileVoiceLabScanJobOptions,
): void {
  if (isActiveVoiceLabJob(job)) onActive(job);
  else onTerminal(job);
}

/** Poll one active job until it reaches a terminal state or its owner unmounts. */
export function pollVoiceLabJob({
  job,
  getJob,
  onJob,
  onError,
  onComplete,
  intervalMs = 1_000,
}: PollVoiceLabJobOptions): () => void {
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const schedule = () => {
    timer = setTimeout(() => { void poll(); }, intervalMs);
  };
  const poll = async () => {
    try {
      const latest = await getJob(job.jobId);
      if (disposed) return;
      onJob(latest);
      if (isActiveVoiceLabJob(latest)) schedule();
      else onComplete(latest);
    } catch (error) {
      if (disposed || !isActiveVoiceLabJob(job)) return;
      onError(error);
      schedule();
    }
  };
  schedule();
  return () => {
    disposed = true;
    if (timer !== undefined) clearTimeout(timer);
  };
}

/** Poll every active job independently and dispose the whole polling group. */
export function pollVoiceLabJobs({
  jobs,
  getJob,
  onJob,
  onError,
  onComplete,
  intervalMs,
}: PollVoiceLabJobsOptions): () => void {
  const stops = jobs
    .filter(isActiveVoiceLabJob)
    .map((job) => pollVoiceLabJob({
      job,
      getJob,
      onJob,
      onError: (error) => onError(error, job),
      onComplete,
      intervalMs,
    }));
  return () => stops.forEach((stop) => stop());
}

interface RefreshVoiceLabAfterMutationOptions {
  startScan: () => Promise<VoiceLabJob>;
  getJob: (jobId: string) => Promise<VoiceLabJob>;
  invalidate: () => Promise<unknown>;
  onJob?: (job: VoiceLabJob) => void;
  wait?: () => Promise<void>;
}

/**
 * Replaces the route's remembered inventory after a successful writer
 * operation. Query invalidation happens only after the new server scan ends.
 */
export async function refreshVoiceLabAfterMutation({
  startScan,
  getJob,
  invalidate,
  onJob = () => {},
  wait = () => new Promise<void>((resolve) => window.setTimeout(resolve, 1_000)),
}: RefreshVoiceLabAfterMutationOptions): Promise<VoiceLabJob> {
  let current = await startScan();
  onJob(current);
  while (isActiveVoiceLabJob(current)) {
    current = await getJob(current.jobId);
    onJob(current);
    if (isActiveVoiceLabJob(current)) await wait();
  }
  if (current.status !== "complete") {
    throw new Error(current.lastMessage || "Voice Lab inventory refresh failed.");
  }
  await invalidate();
  return current;
}
