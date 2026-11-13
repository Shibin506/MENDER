"""Queue worker: claims jobs and runs the diagnosis pipeline."""

from __future__ import annotations

import logging
import signal
import time

import httpx
from prometheus_client import start_http_server
from sqlalchemy.orm import Session

from . import metrics
from .config import Settings
from .db import DiagnosisRecord, Job, claim, complete, fail, get_sessionmaker
from .diagnose import ClaudeDiagnoser, Diagnoser
from .github import GitHubAppAuth, GitHubClient
from .pipeline import Pipeline, RetryableError, RunEvent

log = logging.getLogger(__name__)


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, RetryableError | httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500 or (
            code == 403 and exc.response.headers.get("x-ratelimit-remaining") == "0"
        )
    return False


class Worker:
    def __init__(self, settings: Settings, diagnoser: Diagnoser | None, repo_api_factory=None):
        self.settings = settings
        self.sessions = get_sessionmaker(settings.database_url)
        self.pipeline = Pipeline(settings, diagnoser)
        self._stop = False
        if repo_api_factory is None:
            http = httpx.Client(timeout=30)
            auth = GitHubAppAuth(settings.github_app_id, settings.private_key(),
                                 settings.github_api_url, http)

            def repo_api_factory(installation_id: int, repo: str):
                return GitHubClient(auth, installation_id, repo)

        self.repo_api_factory = repo_api_factory

    def stop(self, *_):
        log.info("shutdown requested, finishing current job")
        self._stop = True

    def process(self, session: Session, job: Job) -> None:
        ev = RunEvent.from_payload(job.payload)
        gh = self.repo_api_factory(ev.installation_id, ev.repo)
        started = time.monotonic()
        results, pr_number, comment_id = self.pipeline.run(gh, ev)
        metrics.RUN_SECONDS.observe(time.monotonic() - started)
        for r in results:
            d, u = r.diagnosis, r.usage
            session.add(DiagnosisRecord(
                repo=ev.repo, pr_number=pr_number, run_id=ev.run_id, run_attempt=ev.run_attempt,
                head_sha=ev.head_sha, job_name=r.job_name, category=d.category,
                confidence=d.confidence, summary=d.summary, used_llm=r.used_llm,
                patch_status=r.patch_status, result=d.model_dump(),
                model=u.model if u else None,
                input_tokens=u.input_tokens if u else 0,
                output_tokens=u.output_tokens if u else 0,
                cost_usd=u.cost_usd if u else None, latency_ms=u.latency_ms if u else 0,
                comment_id=comment_id,
            ))
            metrics.DIAGNOSES.labels(d.category, str(r.used_llm).lower()).inc()

    def run_once(self) -> bool:
        """Process at most one job. Returns True if a job was claimed."""
        with self.sessions() as session:
            job = claim(session, self.settings.job_visibility_timeout_seconds)
            if job is None:
                return False
            log.info("job %s (%s) attempt %d", job.id, job.dedupe_key, job.attempts)
            try:
                self.process(session, job)
                complete(session, job)
                metrics.JOBS.labels("done").inc()
            except Exception as e:  # noqa: BLE001 - every failure is recorded on the job
                session.rollback()
                retry = is_retryable(e)
                log.exception("job %s failed (retryable=%s)", job.id, retry)
                fail(session, job, f"{type(e).__name__}: {e}", retryable=retry)
                metrics.JOBS.labels("retry" if retry and job.status == "queued" else "dead").inc()
            return True

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        start_http_server(self.settings.worker_metrics_port)
        log.info("worker started, metrics on :%d", self.settings.worker_metrics_port)
        while not self._stop:
            if not self.run_once():
                time.sleep(self.settings.worker_poll_seconds)


def build_diagnoser(settings: Settings, enabled: bool = True) -> Diagnoser | None:
    if not enabled:
        return None
    return ClaudeDiagnoser(settings.model, settings.effort, settings.max_output_tokens)
