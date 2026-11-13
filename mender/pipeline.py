"""End-to-end handling of one failed workflow run."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Protocol

import anthropic

from . import metrics
from .classify import Category, Classification, classify
from .config import Settings
from .diagnose import Diagnoser, Diagnosis, DiagnosisInput, DiagnosisRefused, FileLocation
from .github import FailedJob
from .logparse import Extraction, extract
from .patch import dry_run
from .render import JobResult, render_comment

log = logging.getLogger(__name__)

MAX_JOBS_PER_RUN = 3


class RepoAPI(Protocol):
    def failed_jobs(self, run_id: int) -> list[FailedJob]: ...
    def job_log(self, job_id: int) -> str: ...
    def rerun_failed_jobs(self, run_id: int) -> None: ...
    def prs_for_commit(self, sha: str) -> list[int]: ...
    def pull_request(self, number: int) -> dict: ...
    def pull_request_diff(self, number: int) -> str: ...
    def file_at(self, path: str, ref: str) -> str | None: ...
    def upsert_comment(self, pr_number: int, body: str) -> int: ...


class RetryableError(RuntimeError):
    """Transient failure; the job should be retried with backoff."""


@dataclass
class RunEvent:
    repo: str
    installation_id: int
    run_id: int
    run_attempt: int
    head_sha: str
    html_url: str
    pr_numbers: list[int]

    @classmethod
    def from_payload(cls, payload: dict) -> RunEvent:
        run = payload["workflow_run"]
        return cls(
            repo=payload["repository"]["full_name"],
            installation_id=payload["installation"]["id"],
            run_id=run["id"],
            run_attempt=run.get("run_attempt", 1),
            head_sha=run["head_sha"],
            html_url=run.get("html_url", ""),
            pr_numbers=[pr["number"] for pr in run.get("pull_requests", [])],
        )


def truncate_diff(diff: str, budget: int) -> tuple[str, bool]:
    """Keep whole per-file sections of a diff until the budget is spent."""
    if len(diff) <= budget:
        return diff, False
    sections = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    kept, used = [], 0
    for s in sections:
        if used + len(s) > budget:
            continue  # skip oversized files (lockfiles, snapshots) but keep looking
        kept.append(s)
        used += len(s)
    return "".join(kept), True


def changed_files(diff: str) -> list[str]:
    return re.findall(r"^\+\+\+ b/(.+)$", diff, flags=re.MULTILINE)


def heuristic_diagnosis(extraction: Extraction, c: Classification) -> Diagnosis:
    """A diagnosis built only from the extractor, used for flakes and as a fallback."""
    # Show the sharpest excerpt verbatim (indentation matters for compiler carets).
    evidence: list[str] = []
    if extraction.excerpts:
        top = max(extraction.excerpts, key=lambda e: e.score)
        evidence = [ln[:300] for ln in top.text.splitlines()[:15]]
    flaky = c.likely_flaky
    fix = {
        Category.INFRA_FLAKE: "Re-run the failed jobs. If it keeps failing, check the runner "
        "and external services rather than the PR's code.",
        Category.TIMEOUT: "Re-run once. If it times out again, look for a hung test or raise "
        "`timeout-minutes` for the job.",
    }.get(c.category, "See the evidence below.")
    return Diagnosis(
        category=c.category.value,
        summary=f"Likely {c.category.value.replace('_', ' ')}: {c.reason}.",
        root_cause=(
            "This failure matches a known transient pattern, so no model call was made."
            if flaky
            else "Automatic analysis was not available for this failure; the extracted "
            "error lines are shown below."
        ),
        evidence=evidence,
        culprit_files=[FileLocation(path=r.path, line=r.line) for r in extraction.file_refs[:3]],
        caused_by_pr=not flaky,
        fix=fix,
        patch="",
        confidence="medium" if c.confidence >= 0.8 else "low",
    )


class Pipeline:
    def __init__(self, settings: Settings, diagnoser: Diagnoser | None):
        self.settings = settings
        self.diagnoser = diagnoser

    def _context_files(
        self, gh: RepoAPI, extraction: Extraction, pr_files: list[str], sha: str
    ) -> dict[str, str]:
        s = self.settings
        refs = [r.path for r in extraction.file_refs]
        # Files that both appear in the log and were changed by the PR come first.
        ordered = [p for p in refs if p in pr_files] + [p for p in refs if p not in pr_files]
        files: dict[str, str] = {}
        used = 0
        for path in dict.fromkeys(ordered):
            if len(files) >= s.max_context_files:
                break
            content = gh.file_at(path, sha)
            if content is None or used + len(content) > s.file_context_budget_chars:
                continue
            files[path] = content
            used += len(content)
        return files

    def diagnose_job(
        self, gh: RepoAPI, ev: RunEvent, job: FailedJob, pr: dict, diff: str, truncated: bool
    ) -> JobResult:
        raw = gh.job_log(job.id)
        extraction = extract(raw, self.settings.log_budget_chars)
        c = classify(extraction)
        metrics.LOG_COMPRESSION.observe(extraction.compression)

        if (c.likely_flaky and c.confidence >= 0.8) or self.diagnoser is None:
            return JobResult(job.name, job.html_url, job.failed_step,
                             heuristic_diagnosis(extraction, c), used_llm=False)

        files = self._context_files(gh, extraction, changed_files(diff), ev.head_sha)
        inp = DiagnosisInput(
            repo=ev.repo, pr_number=pr.get("number"), pr_title=pr.get("title", ""),
            job_name=job.name, failed_step=job.failed_step, classification=c,
            log_excerpt=extraction.text, diff=diff, diff_truncated=truncated, files=files,
        )
        try:
            diagnosis, usage = self.diagnoser.diagnose(inp)
        except (anthropic.RateLimitError, anthropic.InternalServerError,
                anthropic.APIConnectionError) as e:
            raise RetryableError(f"Claude API transient error: {e}") from e
        except (DiagnosisRefused, anthropic.APIStatusError, RuntimeError) as e:
            log.warning("LLM diagnosis failed for job %s, using heuristic: %s", job.id, e)
            metrics.LLM_FAILURES.labels(type(e).__name__).inc()
            return JobResult(job.name, job.html_url, job.failed_step,
                             heuristic_diagnosis(extraction, c), used_llm=False)

        metrics.LLM_TOKENS.labels("input").inc(usage.input_tokens)
        metrics.LLM_TOKENS.labels("output").inc(usage.output_tokens)
        metrics.LLM_TOKENS.labels("cache_read").inc(usage.cache_read_tokens)
        if usage.cost_usd:
            metrics.LLM_COST.inc(usage.cost_usd)

        result = JobResult(job.name, job.html_url, job.failed_step, diagnosis, used_llm=True,
                           usage=usage)
        if diagnosis.patch.strip():
            def read(path: str) -> str | None:
                return files[path] if path in files else gh.file_at(path, ev.head_sha)

            applied = dry_run(diagnosis.patch, read)
            result.patch_status = "valid" if applied.ok else "invalid"
            result.patch_error = applied.error
            metrics.PATCHES.labels(result.patch_status).inc()
        return result

    def run(self, gh: RepoAPI, ev: RunEvent) -> tuple[list[JobResult], int | None, str | None]:
        """Returns (results, pr_number, comment_id)."""
        prs = ev.pr_numbers or gh.prs_for_commit(ev.head_sha)
        if not prs:
            log.info("run %s on %s has no open PR, skipping", ev.run_id, ev.repo)
            return [], None, None
        pr_number = prs[0]
        pr = gh.pull_request(pr_number)
        if pr.get("head", {}).get("sha", ev.head_sha) != ev.head_sha:
            log.info("PR #%s has moved past %s, skipping stale run", pr_number, ev.head_sha[:7])
            return [], pr_number, None

        jobs = gh.failed_jobs(ev.run_id)
        if not jobs:
            return [], pr_number, None
        diff, truncated = truncate_diff(gh.pull_request_diff(pr_number),
                                        self.settings.diff_budget_chars)

        results = [self.diagnose_job(gh, ev, job, pr, diff, truncated)
                   for job in jobs[:MAX_JOBS_PER_RUN]]

        if (self.settings.auto_rerun_flaky and ev.run_attempt == 1
                and all(not r.diagnosis.caused_by_pr and not r.used_llm for r in results)):
            gh.rerun_failed_jobs(ev.run_id)
            for r in results:
                r.rerun_requested = True

        body = render_comment(results, ev.head_sha, ev.html_url,
                              [j.name for j in jobs[MAX_JOBS_PER_RUN:]])
        if self.settings.dry_run:
            log.info("dry run, comment for %s#%s:\n%s", ev.repo, pr_number, body)
            return results, pr_number, None
        return results, pr_number, gh.upsert_comment(pr_number, body)
