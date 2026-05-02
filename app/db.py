import os
import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tmp")
DB_PATH = os.environ.get("INFRA_AGENT_DB_PATH") or os.path.join(DB_DIR, "infra_agent.db")

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(DB_DIR, exist_ok=True)
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _init_schema(_local.conn)
    return _local.conn


def _init_schema(conn: sqlite3.Connection):
    conn.execute("""
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
    conn.commit()


def load_job(job_id: str) -> Optional[dict]:
    row = _get_conn().execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if not row:
        return None
    return _row_to_dict(row)


def list_jobs(limit: int = 50) -> list[dict]:
    rows = _get_conn().execute(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def upsert_job(job_id: str, **fields):
    existing = load_job(job_id)
    conn = _get_conn()
    if existing:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [job_id]
        conn.execute(f"UPDATE jobs SET {set_clause} WHERE job_id = ?", values)
    else:
        keys = ["job_id"] + list(fields.keys())
        placeholders = ", ".join("?" for _ in keys)
        values = [job_id] + list(fields.values())
        conn.execute(f"INSERT INTO jobs ({', '.join(keys)}) VALUES ({placeholders})", values)
    conn.commit()


def delete_job(job_id: str):
    _get_conn().execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
    _get_conn().commit()


def delete_old_jobs(keep_days: int = 7):
    import datetime
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=keep_days)).isoformat()
    _get_conn().execute("DELETE FROM jobs WHERE created_at < ? AND status IN ('completed', 'failed')", (cutoff,))
    _get_conn().commit()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for field in ("result",):
        if d.get(field) and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d
