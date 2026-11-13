"""Concurrency test for the SKIP LOCKED queue. Needs a real Postgres:

    MENDER_TEST_POSTGRES_URL=postgresql+psycopg://user@host/db pytest tests/test_queue_postgres.py
"""

import os
import threading
from collections import Counter

import pytest

from mender.db import Base, claim, complete, enqueue, get_sessionmaker

PG_URL = os.environ.get("MENDER_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="MENDER_TEST_POSTGRES_URL not set")


def test_concurrent_workers_never_double_claim():
    Session = get_sessionmaker(PG_URL)
    engine = Session.kw["bind"]
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    n_jobs, n_workers = 300, 8
    with Session() as s:
        for i in range(n_jobs):
            enqueue(s, "diagnose_run", f"k{i}", {"i": i})

    claimed: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_workers)

    def worker():
        barrier.wait()  # start together to maximise contention
        with Session() as s:
            while (job := claim(s)) is not None:
                with lock:
                    claimed.append(job.id)
                complete(s, job)

    threads = [threading.Thread(target=worker) for _ in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    counts = Counter(claimed)
    assert len(counts) == n_jobs, "every job claimed"
    assert max(counts.values()) == 1, "no job claimed twice"
