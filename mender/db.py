"""Persistence and a Postgres-backed job queue.

Webhook handlers must answer GitHub within 10 seconds, and a diagnosis takes
longer than that, so the webhook only records a job and returns 202. Workers
claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`, so any number of workers
can run without double-processing and without a separate broker. Jobs whose
worker died are reclaimed after a visibility timeout. Failures retry with
exponential backoff and jitter, then park as `dead`.

SQLite works for local development and tests (it ignores the row lock, which
is fine with a single worker).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    or_,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Delivery(Base):
    """Seen webhook deliveries; GitHub redelivers on timeouts and manual retries."""

    __tablename__ = "deliveries"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32))
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    run_after: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DiagnosisRecord(Base):
    __tablename__ = "diagnoses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo: Mapped[str] = mapped_column(String(255), index=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_id: Mapped[int] = mapped_column(Integer)
    run_attempt: Mapped[int] = mapped_column(Integer, default=1)
    head_sha: Mapped[str] = mapped_column(String(40))
    job_name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[str] = mapped_column(String(16))
    summary: Mapped[str] = mapped_column(Text)
    used_llm: Mapped[bool] = mapped_column(Boolean, default=False)
    patch_status: Mapped[str] = mapped_column(String(16), default="none")  # none|valid|invalid
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    comment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


@lru_cache
def get_sessionmaker(url: str) -> sessionmaker[Session]:
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


# --- queue operations ------------------------------------------------------

def record_delivery(session: Session, delivery_id: str, event: str) -> bool:
    """Returns False if this delivery was already seen."""
    session.add(Delivery(id=delivery_id, event=event))
    try:
        session.commit()
        return True
    except IntegrityError:
        session.rollback()
        return False


def enqueue(
    session: Session, kind: str, dedupe_key: str, payload: dict, max_attempts: int = 4
) -> Job | None:
    """Insert a job unless one with the same dedupe key exists. Returns None if duplicate."""
    job = Job(kind=kind, dedupe_key=dedupe_key, payload=payload, max_attempts=max_attempts)
    session.add(job)
    try:
        session.commit()
        return job
    except IntegrityError:
        session.rollback()
        return None


def claim(session: Session, visibility_timeout: int = 600) -> Job | None:
    now = utcnow()
    stale = now - timedelta(seconds=visibility_timeout)
    stmt = (
        select(Job)
        .where(
            or_(
                (Job.status == "queued") & (Job.run_after <= now),
                (Job.status == "running") & (Job.locked_at < stale),
            )
        )
        .order_by(Job.run_after, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = session.execute(stmt).scalar_one_or_none()
    if job is None:
        session.rollback()
        return None
    job.status = "running"
    job.locked_at = now
    job.attempts += 1
    session.commit()
    return job


def complete(session: Session, job: Job) -> None:
    job.status = "done"
    job.locked_at = None
    job.last_error = None
    session.commit()


def backoff_seconds(attempt: int, base: float = 15.0, cap: float = 900.0) -> float:
    """Full-jitter exponential backoff."""
    return random.uniform(0, min(cap, base * 2 ** (attempt - 1)))


def fail(session: Session, job: Job, error: str, retryable: bool = True) -> None:
    job.last_error = error[:4000]
    job.locked_at = None
    if retryable and job.attempts < job.max_attempts:
        job.status = "queued"
        job.run_after = utcnow() + timedelta(seconds=backoff_seconds(job.attempts))
    else:
        job.status = "dead"
    session.commit()
