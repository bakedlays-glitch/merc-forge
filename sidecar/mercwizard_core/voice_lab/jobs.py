"""Small bounded executor for cooperative Voice Lab background work."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable, Hashable, Literal
from uuid import uuid4


JobStatus = Literal["queued", "running", "cancelling", "cancelled", "complete", "failed"]
JobWork = Callable[[Callable[[int, int, str], None], Callable[[], bool]], Any]


@dataclass
class VoiceLabJob:
    job_id: str
    kind: str
    status: JobStatus = "queued"
    completed: int = 0
    total: int = 0
    last_message: str = "queued"
    cancel_requested: bool = False
    preview_asset_id: str | None = None
    result: Any = field(default=None, repr=False)
    future: Future[Any] | None = field(default=None, repr=False)
    active_scan_key: Hashable | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        public = {
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "completed": self.completed,
            "total": self.total,
            "last_message": self.last_message,
            # Compatibility alias for clients that adopted the initial route.
            "message": self.last_message,
            "cancel_requested": self.cancel_requested,
        }
        if self.preview_asset_id is not None: public["preview_asset_id"] = self.preview_asset_id
        return public


class VoiceLabJobs:
    """Own the two bounded scan workers and one serialized transcriber."""

    scan_worker_count = 2
    transcription_worker_count = 1

    def __init__(self) -> None:
        self._lock = RLock()
        self._jobs: dict[str, VoiceLabJob] = {}
        self._active_scan_jobs: dict[Hashable, str] = {}
        self._scan_executor = ThreadPoolExecutor(
            max_workers=self.scan_worker_count, thread_name_prefix="voice-lab-scan"
        )
        self._transcription_executor = ThreadPoolExecutor(
            max_workers=self.transcription_worker_count, thread_name_prefix="voice-lab-transcribe"
        )

    def submit_scan(self, work: JobWork, *, total: int = 0, kind: str = "scan", preview_asset_id: str | None = None, key: Hashable | None = None) -> VoiceLabJob:
        return self._submit(
            self._scan_executor, kind, work, total,
            preview_asset_id=preview_asset_id,
            active_scan_key=key if kind == "scan" else None,
        )

    def submit_transcription(self, work: JobWork, *, total: int = 0) -> VoiceLabJob:
        return self._submit(self._transcription_executor, "transcription", work, total)

    def submit_analysis(self, work: JobWork, *, total: int = 0) -> VoiceLabJob:
        """Serialize transcript-assisted analysis with standalone transcription."""
        return self._submit(self._transcription_executor, "analysis", work, total)

    def get(self, job_id: str) -> VoiceLabJob:
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as exc:
                raise KeyError("unknown Voice Lab job") from exc

    def cancel(self, job_id: str) -> VoiceLabJob:
        with self._lock:
            job = self.get(job_id)
            if job.status not in {"complete", "failed", "cancelled"}:
                job.cancel_requested = True
                job.status = "cancelling"
                job.last_message = "cancellation requested"
            return job

    def wait(self, job_id: str, timeout: float | None = None) -> VoiceLabJob:
        job = self.get(job_id)
        if job.future is not None:
            job.future.result(timeout=timeout)
        return self.get(job_id)

    def shutdown(self) -> None:
        self._scan_executor.shutdown(wait=True, cancel_futures=False)
        self._transcription_executor.shutdown(wait=True, cancel_futures=False)

    def _submit(self, executor: ThreadPoolExecutor, kind: str, work: JobWork, total: int, *, preview_asset_id: str | None = None, active_scan_key: Hashable | None = None) -> VoiceLabJob:
        with self._lock:
            if active_scan_key is not None:
                active_job_id = self._active_scan_jobs.get(active_scan_key)
                if active_job_id is not None:
                    active_job = self._jobs.get(active_job_id)
                    if active_job is not None and active_job.status not in {"complete", "failed", "cancelled"}:
                        return active_job
                    self._active_scan_jobs.pop(active_scan_key, None)
            job = VoiceLabJob(
                job_id=uuid4().hex, kind=kind, total=max(0, total),
                preview_asset_id=preview_asset_id, active_scan_key=active_scan_key,
            )
            self._jobs[job.job_id] = job
            if active_scan_key is not None:
                self._active_scan_jobs[active_scan_key] = job.job_id
            try:
                job.future = executor.submit(self._run, job.job_id, work)
            except BaseException:
                # Executor shutdown/admission failure must not leave a ghost
                # keyed job that every later request would incorrectly reuse.
                self._jobs.pop(job.job_id, None)
                self._retire_active_scan_key(job)
                raise
        return job

    def _retire_active_scan_key(self, job: VoiceLabJob) -> None:
        if (
            job.active_scan_key is not None
            and self._active_scan_jobs.get(job.active_scan_key) == job.job_id
        ):
            self._active_scan_jobs.pop(job.active_scan_key, None)

    def _run(self, job_id: str, work: JobWork) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.cancel_requested:
                job.status = "cancelled"
                job.last_message = "cancelled before start"
                self._retire_active_scan_key(job)
                return
            job.status = "running"
            job.last_message = "running"

        def progress(completed: int, total: int, message: str) -> None:
            with self._lock:
                current = self._jobs[job_id]
                current.completed = max(0, completed)
                current.total = max(current.completed, total)
                current.last_message = message

        def cancelled() -> bool:
            with self._lock:
                return self._jobs[job_id].cancel_requested

        try:
            result = work(progress, cancelled)
        except Exception as exc:  # Worker failures must remain inspectable but path-safe.
            with self._lock:
                job = self._jobs[job_id]
                if job.cancel_requested:
                    job.status = "cancelled"
                    job.last_message = "cancelled"
                else:
                    job.status = "failed"
                    job.last_message = f"{type(exc).__name__}"
                self._retire_active_scan_key(job)
            return
        with self._lock:
            job = self._jobs[job_id]
            job.result = result
            if job.cancel_requested:
                job.status = "cancelled"
                job.last_message = "cancelled"
            else:
                job.status = "complete"
                job.completed = job.total
                job.last_message = "complete"
            self._retire_active_scan_key(job)
