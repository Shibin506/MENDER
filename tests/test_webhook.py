import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mender.app import app
from mender.config import get_settings
from mender.db import Job

from .conftest import run_payload


@pytest.fixture
def client(settings):
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()


def post(client, payload, event="workflow_run", delivery="d-1", secret="s3cret", sig=None):
    body = json.dumps(payload).encode()
    if sig is None:
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhooks/github", content=body, headers={
        "X-GitHub-Event": event, "X-GitHub-Delivery": delivery, "X-Hub-Signature-256": sig,
        "Content-Type": "application/json",
    })


def test_rejects_bad_signature(client):
    assert post(client, run_payload(), secret="wrong").status_code == 401
    assert post(client, run_payload(), sig="").status_code == 401


def test_ping(client):
    r = post(client, {"zen": "hi"}, event="ping")
    assert r.status_code == 200 and r.json()["status"] == "pong"


def test_ignores_successful_runs(client, session):
    r = post(client, run_payload(conclusion="success"))
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert session.scalars(select(Job)).all() == []


def test_enqueues_failed_run(client, session):
    r = post(client, run_payload())
    assert r.status_code == 202 and r.json()["status"] == "enqueued"
    job = session.scalars(select(Job)).one()
    assert job.dedupe_key == "run:acme/shop:555:1"
    assert job.status == "queued"


def test_redelivery_and_duplicate_runs_are_deduplicated(client, session):
    assert post(client, run_payload(), delivery="d-1").status_code == 202
    assert post(client, run_payload(), delivery="d-1").json()["status"] == "duplicate"
    # Same run, new delivery id (e.g. GitHub retried after a timeout)
    assert post(client, run_payload(), delivery="d-2").json()["status"] == "duplicate"
    # A re-run attempt is a new job
    assert post(client, run_payload(attempt=2), delivery="d-3").json()["status"] == "enqueued"
    assert len(session.scalars(select(Job)).all()) == 2


def test_health_and_stats(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/stats").json()["diagnoses"] == 0
    assert "mender_webhooks_total" in client.get("/metrics").text
