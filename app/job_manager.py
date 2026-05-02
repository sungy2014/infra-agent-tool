import uuid
import json
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

from app.db import upsert_job, load_job, list_jobs as db_list_jobs


_default_manager = None


def get_manager():
    global _default_manager
    if _default_manager is None:
        _default_manager = JobManager()
    return _default_manager


def set_manager(mgr):
    global _default_manager
    _default_manager = mgr


class JobManager:
    def __init__(self):
        self._input_events: dict[str, threading.Event] = {}
        self._input_data: dict[str, str] = {}

    def create_job(self, func: Callable, **kwargs) -> str:
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        upsert_job(job_id, status="queued", created_at=now)
        thread = threading.Thread(
            target=self._run_job, args=(job_id, func), kwargs=kwargs, daemon=True
        )
        thread.start()
        return job_id

    def pause_for_input(self, job_id: str, question: str) -> str:
        upsert_job(job_id, status="awaiting_input", pending_question=question)
        event = threading.Event()
        self._input_events[job_id] = event
        event.wait()
        answer = self._input_data.pop(job_id, "")
        self._input_events.pop(job_id, None)
        upsert_job(job_id, status="running", pending_question=None)
        return answer

    def submit_input(self, job_id: str, answer: str) -> bool:
        job = load_job(job_id)
        if not job or job.get("status") != "awaiting_input":
            return False
        self._input_data[job_id] = answer
        event = self._input_events.get(job_id)
        if event:
            event.set()
            return True
        return False

    def _run_job(self, job_id: str, func: Callable, **kwargs):
        now = datetime.now(timezone.utc).isoformat()
        upsert_job(job_id, status="running", started_at=now)
        try:
            result = func(job_id=job_id, **kwargs)
            upsert_job(
                job_id,
                status="completed",
                result=json.dumps(result),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:
            upsert_job(
                job_id,
                status="failed",
                error=str(e),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

    def get_job(self, job_id: str) -> Optional[dict]:
        return load_job(job_id)

    def list_jobs(self) -> list[dict]:
        return db_list_jobs()
