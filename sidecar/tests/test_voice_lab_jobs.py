"""Behavioral tests for bounded, cooperative Voice Lab jobs."""
from __future__ import annotations

from threading import Barrier, Event, Thread

from mercwizard_core.voice_lab.jobs import VoiceLabJobs


def test_job_cancellation_is_cooperative_and_reported() -> None:
    started = Event()
    release = Event()
    jobs = VoiceLabJobs()
    try:
        def work(progress, cancelled):
            started.set()
            release.wait(timeout=2)
            if cancelled():
                return "cancelled"
            progress(1, 1, "finished")
            return "finished"

        job = jobs.submit_scan(work, total=1)
        assert started.wait(timeout=2)
        assert jobs.cancel(job.job_id).cancel_requested is True
        release.set()
        assert jobs.wait(job.job_id, timeout=2).status == "cancelled"
    finally:
        jobs.shutdown()


def test_transcriptions_use_a_serial_worker() -> None:
    jobs = VoiceLabJobs()
    try:
        assert jobs.scan_worker_count == 2
        assert jobs.transcription_worker_count == 1
    finally:
        jobs.shutdown()


def test_public_job_status_exposes_last_message_with_legacy_alias() -> None:
    """A generic message field leaves clients unable to rely on the documented status contract."""
    jobs = VoiceLabJobs()
    try:
        job = jobs.submit_scan(lambda progress, cancelled: progress(1, 1, "audit complete"), total=1)
        public = jobs.wait(job.job_id, timeout=2).public()

        assert public["last_message"] == "complete"
        assert public["message"] == "complete"
    finally:
        jobs.shutdown()


def test_analysis_shares_the_serial_transcription_lane() -> None:
    """Analysis must not run a second speech model beside a transcription job."""
    first_started = Event()
    release_first = Event()
    analysis_started = Event()
    jobs = VoiceLabJobs()
    try:
        first = jobs.submit_transcription(
            lambda _progress, _cancelled: (
                first_started.set(), release_first.wait(timeout=2)
            ),
            total=1,
        )
        assert first_started.wait(timeout=2)

        analysis = jobs.submit_analysis(
            lambda _progress, _cancelled: analysis_started.set(),
            total=3,
        )
        assert analysis.kind == "analysis"
        assert analysis_started.wait(timeout=.1) is False

        release_first.set()
        jobs.wait(first.job_id, timeout=2)
        assert jobs.wait(analysis.job_id, timeout=2).status == "complete"
        assert analysis_started.is_set()
    finally:
        release_first.set()
        jobs.shutdown()


def test_simultaneous_scan_starts_with_the_same_key_reuse_one_active_job() -> None:
    """Duplicate requests for one install/bank must not occupy both scan workers."""
    gate = Barrier(3)
    started = Event()
    release = Event()
    returned = []
    jobs = VoiceLabJobs()
    try:
        def work(_progress, _cancelled):
            started.set()
            release.wait(timeout=2)

        def submit() -> None:
            gate.wait(timeout=2)
            returned.append(jobs.submit_scan(work, key=("fixture", 108)))

        first = Thread(target=submit)
        second = Thread(target=submit)
        first.start()
        second.start()
        gate.wait(timeout=2)
        first.join(timeout=2)
        second.join(timeout=2)

        assert len(returned) == 2
        assert returned[0].job_id == returned[1].job_id
        assert started.wait(timeout=2)
    finally:
        release.set()
        jobs.shutdown()


def test_scan_starts_for_different_bank_keys_can_run_together() -> None:
    """Admission must not collapse selected-bank scans for different banks."""
    first_started = Event()
    second_started = Event()
    release = Event()
    jobs = VoiceLabJobs()
    try:
        def first_work(_progress, _cancelled):
            first_started.set()
            release.wait(timeout=2)

        def second_work(_progress, _cancelled):
            second_started.set()
            release.wait(timeout=2)

        first = jobs.submit_scan(first_work, key=("fixture", 108))
        second = jobs.submit_scan(second_work, key=("fixture", 109))

        assert first.job_id != second.job_id
        assert first_started.wait(timeout=2)
        assert second_started.wait(timeout=2)
    finally:
        release.set()
        jobs.shutdown()


def test_keyed_non_scan_work_is_not_deduped() -> None:
    """Audit and preview work do not share the scan admission contract."""
    release = Event()
    jobs = VoiceLabJobs()
    try:
        first = jobs.submit_scan(
            lambda _progress, _cancelled: release.wait(timeout=2),
            kind="audit", key=("fixture", 108),
        )
        second = jobs.submit_scan(
            lambda _progress, _cancelled: release.wait(timeout=2),
            kind="audit", key=("fixture", 108),
        )

        assert first.job_id != second.job_id
    finally:
        release.set()
        jobs.shutdown()


def test_scan_key_can_start_again_after_its_prior_job_is_terminal() -> None:
    """A completed scan must not suppress a later refresh of the same bank."""
    jobs = VoiceLabJobs()
    try:
        first = jobs.submit_scan(lambda _progress, _cancelled: None, key=("fixture", 108))
        assert jobs.wait(first.job_id, timeout=2).status == "complete"

        retry = jobs.submit_scan(lambda _progress, _cancelled: None, key=("fixture", 108))

        assert retry.job_id != first.job_id
        assert jobs.wait(retry.job_id, timeout=2).status == "complete"
    finally:
        jobs.shutdown()


def test_failed_executor_admission_does_not_leave_a_ghost_scan_key() -> None:
    """A submit failure must be retryable instead of pinning an absent job."""
    jobs = VoiceLabJobs()

    class RejectingExecutor:
        def submit(self, *_args, **_kwargs):
            raise RuntimeError("executor unavailable")

    real_executor = jobs._scan_executor
    jobs._scan_executor = RejectingExecutor()  # type: ignore[assignment]
    try:
        for _ in range(2):
            try:
                jobs.submit_scan(
                    lambda _progress, _cancelled: None,
                    key=("fixture", 108),
                )
            except RuntimeError as error:
                assert str(error) == "executor unavailable"
            else:
                raise AssertionError("executor rejection was swallowed")
        assert jobs._jobs == {}
        assert jobs._active_scan_jobs == {}
    finally:
        jobs._scan_executor = real_executor
        jobs.shutdown()
