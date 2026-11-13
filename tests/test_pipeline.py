import anthropic
import httpx
import pytest
from sqlalchemy import select

from mender.db import DiagnosisRecord, Job, enqueue
from mender.diagnose import DiagnosisRefused
from mender.pipeline import Pipeline, RetryableError, RunEvent, truncate_diff
from mender.worker import Worker

from .conftest import FLAKY_LOG, FakeDiagnoser, FakeRepo, run_payload


def run(settings, repo, diagnoser, **payload_kw):
    return Pipeline(settings, diagnoser).run(repo, RunEvent.from_payload(run_payload(**payload_kw)))


def test_happy_path_posts_verified_patch(settings):
    repo, diag = FakeRepo(), FakeDiagnoser()
    results, pr, comment_id = run(settings, repo, diag)
    assert pr == 7 and comment_id == 999
    assert results[0].patch_status == "valid"
    body = repo.comments[7]
    assert "Suggested patch (applies cleanly to <code>aaaaaaa</code>)" in body
    assert "Coupon replaces the member discount" in body
    # the log-referenced file that the PR changed was sent as context
    assert "src/shop/pricing.py" in diag.calls[0].files
    assert "E       assert 90.0 == 81.0" in diag.calls[0].log_excerpt


def test_patch_that_does_not_apply_is_hidden(settings):
    repo = FakeRepo()
    bad = "--- a/src/shop/pricing.py\n+++ b/src/shop/pricing.py\n@@ -1,1 +1,1 @@\n-nope\n+yes\n"
    results, _, _ = run(settings, repo, FakeDiagnoser(patch=bad))
    assert results[0].patch_status == "invalid"
    assert "did not apply" in repo.comments[7]
    assert "+yes" not in repo.comments[7]


def test_flaky_failure_skips_the_model(settings):
    repo, diag = FakeRepo(log=FLAKY_LOG), FakeDiagnoser()
    results, _, _ = run(settings, repo, diag)
    assert diag.calls == []
    assert results[0].diagnosis.category == "infra_flake"
    assert not results[0].diagnosis.caused_by_pr


def test_auto_rerun_flaky_only_on_first_attempt(settings):
    settings.auto_rerun_flaky = True
    repo = FakeRepo(log=FLAKY_LOG)
    run(settings, repo, FakeDiagnoser(), attempt=1)
    run(settings, repo, FakeDiagnoser(), attempt=2)
    assert repo.reruns == [555]


def test_refusal_falls_back_to_heuristics(settings):
    repo = FakeRepo()
    results, _, _ = run(settings, repo, FakeDiagnoser(exc=DiagnosisRefused("cyber")))
    assert not results[0].used_llm
    assert results[0].diagnosis.category == "test_failure"
    assert 7 in repo.comments


def test_rate_limit_is_retryable(settings):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.RateLimitError("slow down", response=httpx.Response(429, request=req),
                                   body=None)
    with pytest.raises(RetryableError):
        run(settings, FakeRepo(), FakeDiagnoser(exc=err))


def test_stale_run_is_skipped(settings):
    repo = FakeRepo(head="b" * 40)
    results, _, comment = run(settings, repo, FakeDiagnoser())
    assert results == [] and comment is None and repo.comments == {}


def test_fork_pr_found_via_commit_lookup(settings):
    repo = FakeRepo(prs=(9,))
    _, pr, _ = run(settings, repo, FakeDiagnoser(), prs=())
    assert pr == 9


def test_dry_run_does_not_comment(settings):
    settings.dry_run = True
    repo = FakeRepo()
    _, _, comment = run(settings, repo, FakeDiagnoser())
    assert comment is None and repo.comments == {}


def test_truncate_diff_keeps_whole_files():
    small = "diff --git a/a b/a\n+++ b/a\n+x\n"
    huge = "diff --git a/lock b/lock\n+++ b/lock\n" + "+y\n" * 5000
    out, truncated = truncate_diff(small + huge + small.replace("/a", "/c"), 200)
    assert truncated and "b/lock" not in out and "b/a" in out and "b/c" in out


def test_worker_processes_job_and_records_diagnosis(settings, session):
    enqueue(session, "diagnose_run", "run:acme/shop:555:1", run_payload())
    repo = FakeRepo()
    worker = Worker(settings, FakeDiagnoser(), repo_api_factory=lambda inst, name: repo)
    assert worker.run_once() is True
    assert worker.run_once() is False
    rec = session.scalars(select(DiagnosisRecord)).one()
    assert rec.category == "test_failure" and rec.patch_status == "valid" and rec.used_llm
    assert rec.cost_usd == 0.02 and rec.comment_id == 999
    session.expire_all()
    assert session.scalars(select(Job)).one().status == "done"


def test_worker_marks_github_404_as_dead(settings, session):
    enqueue(session, "diagnose_run", "run:acme/shop:555:1", run_payload())

    class Gone(FakeRepo):
        def failed_jobs(self, run_id):
            req = httpx.Request("GET", "https://api.github.com/x")
            raise httpx.HTTPStatusError("gone", request=req,
                                        response=httpx.Response(404, request=req))

    worker = Worker(settings, FakeDiagnoser(), repo_api_factory=lambda i, n: Gone())
    worker.run_once()
    session.expire_all()
    assert session.scalars(select(Job)).one().status == "dead"
