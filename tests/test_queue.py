from datetime import timedelta

from mender.db import backoff_seconds, claim, complete, enqueue, fail, utcnow


def test_claim_complete(session):
    job = enqueue(session, "diagnose_run", "k1", {"x": 1})
    assert enqueue(session, "diagnose_run", "k1", {"x": 1}) is None  # dedupe
    claimed = claim(session)
    assert claimed.id == job.id and claimed.status == "running" and claimed.attempts == 1
    assert claim(session) is None  # nothing else ready
    complete(session, claimed)
    assert claimed.status == "done"


def test_retry_with_backoff_then_dead(session):
    enqueue(session, "diagnose_run", "k2", {}, max_attempts=2)
    job = claim(session)
    fail(session, job, "boom")
    assert job.status == "queued" and job.run_after > utcnow() - timedelta(seconds=1)
    job.run_after = utcnow()  # skip the wait
    session.commit()
    job = claim(session)
    assert job.attempts == 2
    fail(session, job, "boom again")
    assert job.status == "dead" and "boom again" in job.last_error


def test_non_retryable_goes_straight_to_dead(session):
    enqueue(session, "diagnose_run", "k3", {})
    job = claim(session)
    fail(session, job, "404 logs expired", retryable=False)
    assert job.status == "dead"


def test_stale_running_job_is_reclaimed(session):
    enqueue(session, "diagnose_run", "k4", {})
    job = claim(session)
    job.locked_at = utcnow() - timedelta(seconds=3600)  # worker died mid-job
    session.commit()
    again = claim(session, visibility_timeout=600)
    assert again.id == job.id and again.attempts == 2


def test_backoff_is_bounded():
    assert all(0 <= backoff_seconds(a) <= 900 for a in range(1, 20))
