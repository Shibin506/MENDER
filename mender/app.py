"""Webhook receiver and read API."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import metrics
from .config import Settings, get_settings
from .db import DiagnosisRecord, Job, enqueue, get_sessionmaker, record_delivery
from .github import verify_signature

log = logging.getLogger(__name__)
app = FastAPI(title="Mender", version="0.1.0")

FAILED_CONCLUSIONS = {"failure", "timed_out"}


def get_session(settings: Settings = Depends(get_settings)) -> Iterator[Session]:
    with get_sessionmaker(settings.database_url)() as session:
        yield session


@app.post("/webhooks/github", status_code=202)
async def github_webhook(
    request: Request,
    response: Response,
    x_github_event: str = Header(...),
    x_github_delivery: str = Header(...),
    x_hub_signature_256: str | None = Header(None),
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_session),
):
    body = await request.body()
    if not verify_signature(settings.github_webhook_secret, body, x_hub_signature_256):
        metrics.WEBHOOKS.labels(x_github_event, "bad_signature").inc()
        raise HTTPException(401, "invalid signature")

    payload = await request.json()
    if x_github_event == "ping":
        response.status_code = 200
        return {"status": "pong"}

    run = payload.get("workflow_run") or {}
    if (
        x_github_event != "workflow_run"
        or payload.get("action") != "completed"
        or run.get("conclusion") not in FAILED_CONCLUSIONS
        or run.get("event") not in ("pull_request", "pull_request_target", "push")
    ):
        metrics.WEBHOOKS.labels(x_github_event, "ignored").inc()
        response.status_code = 200
        return {"status": "ignored"}

    if not record_delivery(session, x_github_delivery, x_github_event):
        metrics.WEBHOOKS.labels(x_github_event, "duplicate").inc()
        response.status_code = 200
        return {"status": "duplicate"}

    repo = payload["repository"]["full_name"]
    key = f"run:{repo}:{run['id']}:{run.get('run_attempt', 1)}"
    job = enqueue(session, "diagnose_run", key, payload, settings.job_max_attempts)
    metrics.WEBHOOKS.labels(x_github_event, "enqueued" if job else "duplicate").inc()
    return {"status": "enqueued" if job else "duplicate", "job_id": job.id if job else None}


@app.get("/healthz")
def healthz(session: Session = Depends(get_session)):
    session.execute(select(1))
    return {"status": "ok"}


@app.get("/metrics")
def prometheus_metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/diagnoses")
def list_diagnoses(
    repo: str | None = None, limit: int = 50, session: Session = Depends(get_session)
):
    stmt = select(DiagnosisRecord).order_by(DiagnosisRecord.id.desc()).limit(min(limit, 200))
    if repo:
        stmt = stmt.where(DiagnosisRecord.repo == repo)
    return [
        {
            "id": d.id, "repo": d.repo, "pr_number": d.pr_number, "run_id": d.run_id,
            "head_sha": d.head_sha, "job": d.job_name, "category": d.category,
            "confidence": d.confidence, "summary": d.summary, "used_llm": d.used_llm,
            "patch_status": d.patch_status, "cost_usd": d.cost_usd, "latency_ms": d.latency_ms,
            "created_at": d.created_at.isoformat() + "Z",
        }
        for d in session.execute(stmt).scalars()
    ]


@app.get("/api/stats")
def stats(session: Session = Depends(get_session)):
    total = session.scalar(select(func.count(DiagnosisRecord.id))) or 0
    by_cat = dict(session.execute(
        select(DiagnosisRecord.category, func.count()).group_by(DiagnosisRecord.category)).all())
    llm = session.scalar(select(func.count()).where(DiagnosisRecord.used_llm.is_(True))) or 0
    patches = dict(session.execute(
        select(DiagnosisRecord.patch_status, func.count())
        .group_by(DiagnosisRecord.patch_status)).all())
    avg_cost = session.scalar(
        select(func.avg(DiagnosisRecord.cost_usd)).where(DiagnosisRecord.used_llm.is_(True)))
    avg_latency = session.scalar(
        select(func.avg(DiagnosisRecord.latency_ms)).where(DiagnosisRecord.used_llm.is_(True)))
    queue = dict(session.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    return {
        "diagnoses": total,
        "by_category": by_cat,
        "llm_calls": llm,
        "skipped_llm": total - llm,
        "patches": patches,
        "avg_cost_usd": round(avg_cost, 4) if avg_cost else None,
        "avg_llm_latency_ms": int(avg_latency) if avg_latency else None,
        "queue": queue,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return (Path(__file__).parent / "dashboard.html").read_text()
