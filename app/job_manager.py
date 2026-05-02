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
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}

    def create_job(self, func: Callable, **kwargs) -> str:
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        upsert_job(job_id, status="queued", created_at=now)
        self._cancel_events[job_id] = threading.Event()
        thread = threading.Thread(
            target=self._run_job, args=(job_id, func), kwargs=kwargs, daemon=True
        )
        self._threads[job_id] = thread
        thread.start()
        return job_id

    def is_cancelled(self, job_id: str) -> bool:
        ev = self._cancel_events.get(job_id)
        return ev is not None and ev.is_set()

    def cancel_job(self, job_id: str) -> bool:
        job = load_job(job_id)
        if not job:
            return False
        if job.get("status") in ("completed", "failed", "cancelled"):
            return False
        if job_id in self._cancel_events:
            self._cancel_events[job_id].set()
        ev = self._input_events.get(job_id)
        if ev:
            ev.set()
        self._input_data[job_id] = ""
        upsert_job(job_id, status="cancelled")
        thread = self._threads.get(job_id)
        if thread and thread.is_alive():
            thread.join(timeout=2)
        return True

    def pause_for_input(self, job_id: str, question: str) -> str:
        upsert_job(job_id, status="awaiting_input", pending_question=question)
        event = threading.Event()
        self._input_events[job_id] = event
        waited = event.wait(timeout=600)
        self._input_events.pop(job_id, None)
        if self.is_cancelled(job_id):
            raise RuntimeError("Job was cancelled")
        if not waited:
            raise TimeoutError("User did not respond within 10 minutes")
        answer = self._input_data.pop(job_id, "")
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
        kwargs["job_id"] = job_id
        now = datetime.now(timezone.utc).isoformat()
        upsert_job(job_id, status="running", started_at=now)
        try:
            result = func(**kwargs)
            if self.is_cancelled(job_id):
                return
            upsert_job(
                job_id,
                status="completed",
                result=json.dumps(result),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:
            if self.is_cancelled(job_id):
                return
            upsert_job(
                job_id,
                status="failed",
                error=str(e),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            self._threads.pop(job_id, None)

    def get_job(self, job_id: str) -> Optional[dict]:
        return load_job(job_id)

    def list_jobs(self, limit: int = 50) -> list[dict]:
        return db_list_jobs(limit=limit)
