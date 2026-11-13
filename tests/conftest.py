from __future__ import annotations

import os

import pytest

from mender.config import Settings
from mender.db import Base, get_sessionmaker
from mender.diagnose import Diagnosis, DiagnosisInput, FileLocation, Usage
from mender.github import FailedJob

HEAD = "a" * 40


PG_URL = os.environ.get("MENDER_TEST_POSTGRES_URL")


@pytest.fixture
def settings(tmp_path) -> Settings:
    if PG_URL:
        # Run the whole suite against Postgres when available (CI does this).
        engine = get_sessionmaker(PG_URL).kw["bind"]
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    return Settings(
        database_url=PG_URL or f"sqlite:///{tmp_path}/test.db",
        github_webhook_secret="s3cret",
        github_app_id="1",
        github_private_key="unused",
        _env_file=None,
    )


@pytest.fixture
def session(settings):
    with get_sessionmaker(settings.database_url)() as s:
        yield s


PRICING_PY = """\
def total(cart):
    subtotal = sum(i.price for i in cart.items)
    discount = 0.0
    if cart.member:
        discount = 0.1
    if cart.coupon:
        discount = cart.coupon
    return round(subtotal * (1 - discount), 2)
"""

PYTEST_LOG = """\
2024-05-01T10:00:00.0000000Z ##[group]Run pip install -r requirements.txt
2024-05-01T10:00:01.0000000Z Collecting fastapi==0.110.0
2024-05-01T10:00:02.0000000Z ##[endgroup]
2024-05-01T10:00:03.0000000Z =================================== FAILURES ===================================
2024-05-01T10:00:03.0000000Z __________________________ test_member_discount_stacks _________________________
2024-05-01T10:00:03.0000000Z >       assert total(cart) == 81.0
2024-05-01T10:00:03.0000000Z E       assert 90.0 == 81.0
2024-05-01T10:00:03.0000000Z /home/runner/work/shop/shop/src/shop/pricing.py:7: in total
2024-05-01T10:00:03.0000000Z tests/test_pricing.py:22: AssertionError
2024-05-01T10:00:03.0000000Z FAILED tests/test_pricing.py::test_member_discount_stacks - assert 90.0 == 81.0
2024-05-01T10:00:04.0000000Z ##[error]Process completed with exit code 1.
"""

FLAKY_LOG = """\
2024-05-01T10:00:00.0000000Z Run npm ci
2024-05-01T10:00:01.0000000Z npm error code ECONNRESET
2024-05-01T10:00:01.0000000Z npm error network aborted
2024-05-01T10:00:02.0000000Z ##[error]Process completed with exit code 1.
"""

GOOD_PATCH = """\
--- a/src/shop/pricing.py
+++ b/src/shop/pricing.py
@@ -5,4 +5,4 @@
         discount = 0.1
     if cart.coupon:
-        discount = cart.coupon
+        discount = 1 - (1 - discount) * (1 - cart.coupon)
     return round(subtotal * (1 - discount), 2)
"""


class FakeRepo:
    """In-memory stand-in for GitHubClient."""

    def __init__(self, log: str = PYTEST_LOG, head: str = HEAD, prs=(7,)):
        self.log = log
        self.head = head
        self.prs = list(prs)
        self.comments: dict[int, str] = {}
        self.reruns: list[int] = []
        self.files = {"src/shop/pricing.py": PRICING_PY}

    def failed_jobs(self, run_id):
        return [FailedJob(11, "test (3.12)", "Run pytest", "https://gh/job/11")]

    def job_log(self, job_id):
        return self.log

    def rerun_failed_jobs(self, run_id):
        self.reruns.append(run_id)

    def prs_for_commit(self, sha):
        return self.prs

    def pull_request(self, number):
        return {"number": number, "title": "Stack coupons", "head": {"sha": self.head}}

    def pull_request_diff(self, number):
        return (
            "diff --git a/src/shop/pricing.py b/src/shop/pricing.py\n"
            "--- a/src/shop/pricing.py\n+++ b/src/shop/pricing.py\n"
            "@@ -6,2 +6,2 @@\n-    if cart.coupon and not cart.member:\n+    if cart.coupon:\n"
        )

    def file_at(self, path, ref):
        return self.files.get(path)

    def upsert_comment(self, pr_number, body):
        self.comments[pr_number] = body
        return 999


class FakeDiagnoser:
    def __init__(self, patch: str = GOOD_PATCH, exc: Exception | None = None):
        self.patch = patch
        self.exc = exc
        self.calls: list[DiagnosisInput] = []

    def diagnose(self, inp: DiagnosisInput):
        self.calls.append(inp)
        if self.exc:
            raise self.exc
        return (
            Diagnosis(
                category="test_failure",
                summary="Coupon replaces the member discount instead of stacking with it.",
                root_cause="`total` overwrites `discount` when a coupon is present.",
                evidence=["E       assert 90.0 == 81.0"],
                culprit_files=[FileLocation(path="src/shop/pricing.py", line=7)],
                caused_by_pr=True,
                fix="Combine the discounts multiplicatively.",
                patch=self.patch,
                confidence="high",
            ),
            Usage(input_tokens=3000, output_tokens=400, cost_usd=0.02, latency_ms=4200,
                  model="claude-opus-5-5"),
        )


def run_payload(run_id=555, attempt=1, conclusion="failure", prs=(7,), event="pull_request"):
    return {
        "action": "completed",
        "installation": {"id": 42},
        "repository": {"full_name": "acme/shop"},
        "workflow_run": {
            "id": run_id,
            "run_attempt": attempt,
            "conclusion": conclusion,
            "event": event,
            "head_sha": HEAD,
            "html_url": f"https://github.com/acme/shop/actions/runs/{run_id}",
            "pull_requests": [{"number": n} for n in prs],
        },
    }
