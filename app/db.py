import os
import json
import threading
from typing import Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool

_local = threading.local()


def _get_dsn() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://infra:infra@localhost:5432/infra",
    )


def _get_conn():
    if not hasattr(_local, "conn") or _local.conn is None or _local.conn.closed:
        dsn = _get_dsn()
        _local.conn = psycopg2.connect(dsn)
        _local.conn.autocommit = True
        _init_schema()
    return _local.conn


def _init_schema():
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                result TEXT,
                error TEXT,
                pending_question TEXT
            )
        """)


def load_job(job_id: str) -> Optional[dict]:
    conn = _get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM jobs WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_dict(dict(row))


def list_jobs(limit: int = 50) -> list[dict]:
    conn = _get_conn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT %s", (limit,))
        return [_row_to_dict(dict(r)) for r in cur.fetchall()]


def upsert_job(job_id: str, **fields):
    conn = _get_conn()
    existing = load_job(job_id)
    with conn.cursor() as cur:
        if existing:
            set_clause = ", ".join(f"{k} = %s" for k in fields)
            values = list(fields.values()) + [job_id]
            cur.execute(f"UPDATE jobs SET {set_clause} WHERE job_id = %s", values)
        else:
            keys = ["job_id"] + list(fields.keys())
            placeholders = ", ".join("%s" for _ in keys)
            values = [job_id] + list(fields.values())
            cur.execute(
                f"INSERT INTO jobs ({', '.join(keys)}) VALUES ({placeholders})",
                values,
            )


def delete_old_jobs(keep_days: int = 7):
    import datetime
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=keep_days)).isoformat()
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM jobs WHERE created_at < %s AND status IN ('completed', 'failed')",
            (cutoff,),
        )


def _row_to_dict(row: dict) -> dict:
    for field in ("result",):
        val = row.get(field)
        if val and isinstance(val, str):
            try:
                row[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
    return row
