"""Durable, manager-scoped queue for isolated aerial media analysis."""
from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Jsonb

from storage import database


def create_job(url: str | None, *, created_by: str, original_name: str,
               media_kind: str, media_path: str, sha256: str,
               anchor_gcj: dict[str, float] | None, camera_stabilized: bool = False) -> dict:
    job_id = str(uuid.uuid4())
    with database.connect(url) as conn:
        row = conn.execute(
            "INSERT INTO manager_vision_job(job_id, created_by, original_name, media_kind,"
            " media_path, sha256, anchor_gcj, camera_stabilized, status)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'queued')"
            " RETURNING job_id, status, created_at",
            (job_id, created_by, original_name, media_kind, media_path, sha256,
             Jsonb(anchor_gcj), bool(camera_stabilized)),
        ).fetchone()
    return {**row, "job_id": str(row["job_id"])}


def claim_next_job(url: str | None, worker_id: str, lease_seconds: int = 90) -> dict | None:
    with database.connect(url) as conn:
        conn.execute(
            "UPDATE manager_vision_job SET status='failed', lease_until=NULL,"
            " error=%s, updated_at=now() WHERE status='running' AND lease_until<now() AND attempts>=3",
            (Jsonb({"code": "worker_lease_exhausted", "message": "影像任务多次失去工作租约，请重新提交。"}),),
        )
        row = conn.execute(
            "WITH candidate AS (SELECT job_id FROM manager_vision_job"
            " WHERE attempts<3 AND (status='queued' OR (status='running' AND lease_until < now()))"
            " ORDER BY created_at, job_id FOR UPDATE SKIP LOCKED LIMIT 1)"
            " UPDATE manager_vision_job j SET status='running', worker_id=%s,"
            " lease_until=now()+(%s * interval '1 second'), attempts=attempts+1, updated_at=now()"
            " FROM candidate c WHERE j.job_id=c.job_id"
            " RETURNING j.job_id, j.media_kind, j.media_path, j.anchor_gcj, j.camera_stabilized, j.attempts",
            (worker_id, lease_seconds),
        ).fetchone()
    return {**row, "job_id": str(row["job_id"])} if row else None


def heartbeat_job(url: str | None, job_id: str, worker_id: str, lease_seconds: int = 90) -> bool:
    with database.connect(url) as conn:
        row = conn.execute(
            "UPDATE manager_vision_job SET lease_until=now()+(%s * interval '1 second'), updated_at=now()"
            " WHERE job_id=%s AND worker_id=%s AND status='running' AND lease_until>now()"
            " RETURNING job_id", (lease_seconds, job_id, worker_id),
        ).fetchone()
    return row is not None


def finish_job(url: str | None, job_id: str, worker_id: str, *, status: str,
               result: dict | None = None, error: dict | None = None) -> bool:
    if status not in {"needs_review", "completed", "failed"}:
        raise ValueError("invalid vision job terminal status")
    with database.connect(url) as conn:
        row = conn.execute(
            "UPDATE manager_vision_job SET status=%s, result=%s, error=%s, lease_until=NULL,"
            " updated_at=now() WHERE job_id=%s AND worker_id=%s AND status='running'"
            " AND lease_until>now() RETURNING job_id",
            (status, Jsonb(result) if result is not None else None,
             Jsonb(error) if error is not None else None, job_id, worker_id),
        ).fetchone()
    return row is not None


def get_job(url: str | None, job_id: str) -> dict | None:
    with database.connect(url) as conn:
        row = conn.execute(
            "SELECT job_id, created_by, original_name, media_kind, media_path, sha256, anchor_gcj, camera_stabilized,"
            " status, attempts, result, error, review_status, review_candidate_index, review_note, reviewed_by, reviewed_at,"
            " created_at, updated_at FROM manager_vision_job WHERE job_id=%s", (job_id,),
        ).fetchone()
    return {**row, "job_id": str(row["job_id"])} if row else None


def list_jobs(url: str | None, limit: int = 50) -> list[dict[str, Any]]:
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    with database.connect(url) as conn:
        rows = conn.execute(
            "SELECT job_id, created_by, original_name, media_kind, anchor_gcj, camera_stabilized, status, attempts,"
            " result, error, review_status, review_candidate_index, review_note, reviewed_by, reviewed_at,"
            " created_at, updated_at"
            " FROM manager_vision_job ORDER BY created_at DESC LIMIT %s", (limit,),
        ).fetchall()
    return [{**row, "job_id": str(row["job_id"])} for row in rows]


def review_job(url: str | None, job_id: str, *, review_status: str,
               review_note: str, reviewed_by: str,
               candidate_index: int | None = None) -> dict | None:
    if review_status not in {"confirmed", "dismissed"}:
        raise ValueError("review_status must be confirmed or dismissed")
    if review_status == "confirmed" and (isinstance(candidate_index, bool)
                                          or not isinstance(candidate_index, int)
                                          or candidate_index < 0):
        raise ValueError("confirmed review requires a candidate index")
    if review_status == "dismissed":
        candidate_index = None
    with database.connect(url) as conn:
        row = conn.execute(
            "UPDATE manager_vision_job SET review_status=%s, review_candidate_index=%s,"
            " review_note=%s, reviewed_by=%s,"
            " reviewed_at=now(), updated_at=now() WHERE job_id=%s AND status='needs_review'"
            " AND review_status IS NULL"
            " AND (%s = 'dismissed' OR CASE WHEN jsonb_typeof(result->'candidates') = 'array'"
            " THEN jsonb_array_length(result->'candidates') > %s ELSE false END)"
            " RETURNING job_id, status, review_status, review_candidate_index, review_note, reviewed_by, reviewed_at",
            (review_status, candidate_index, review_note[:1000], reviewed_by, job_id,
             review_status, candidate_index if candidate_index is not None else -1),
        ).fetchone()
    return {**row, "job_id": str(row["job_id"])} if row else None


def heartbeat_worker(url: str | None, worker_id: str, *, ready: bool,
                     status_detail: str | None = None) -> None:
    """Publish vision-process health; the web app never infers readiness from files alone."""
    with database.connect(url) as conn:
        conn.execute(
            "INSERT INTO manager_vision_worker(worker_id, ready, status_detail, heartbeat_at)"
            " VALUES (%s,%s,%s,now()) ON CONFLICT(worker_id) DO UPDATE SET"
            " ready=EXCLUDED.ready, status_detail=EXCLUDED.status_detail, heartbeat_at=now()",
            (worker_id, bool(ready), (status_detail or "")[:500]),
        )


def has_ready_worker(url: str | None, max_age_seconds: int = 30,
                     worker_id: str | None = None) -> bool:
    """Return true only when a healthy worker has renewed its heartbeat recently."""
    worker_filter = " AND worker_id=%s" if worker_id else ""
    parameters = (max(1, int(max_age_seconds)), worker_id) if worker_id else (max(1, int(max_age_seconds)),)
    with database.connect(url) as conn:
        row = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM manager_vision_worker WHERE ready=true"
            " AND heartbeat_at > now() - (%s * interval '1 second')" + worker_filter + ") AS ready",
            parameters,
        ).fetchone()
    return bool(row and row["ready"])
