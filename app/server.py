#!/usr/bin/env python3
import os
import json as jmod
import logging
import warnings
import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional
from contextlib import asynccontextmanager

import jwt
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel

warnings.filterwarnings("ignore", message=".*OpenSSL.*")

from app.config import Config
from app.pipeline.core import run_pipeline
from app.job_manager import JobManager, set_manager
from app.db import list_jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
log = logging.getLogger("infra-agent")

config = Config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting infra-agent server")
    from app.db import delete_old_jobs
    try:
        delete_old_jobs(keep_days=7)
        log.info("cleaned up jobs older than 7 days")
    except Exception as e:
        log.warning("job cleanup failed: %s", e)
    yield
    log.info("shutting down — in-flight jobs will be terminated")


app = FastAPI(title="Infra Agent API", version="1.0.0", lifespan=lifespan)

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ── Auth ──────────────────────────────────────────────────────────────

AUTH_ENABLED = config.auth_enabled
AUTH_USER = config.auth_username
AUTH_PASS = config.auth_password
AUTH_SECRET = hashlib.sha256((config.auth_secret + "_infra_agent_salt").encode()).hexdigest()
TOKEN_EXPIRY_HOURS = 24


def create_token(username: str) -> str:
    payload = {
        "sub": username,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS),
    }
    return jwt.encode(payload, AUTH_SECRET, algorithm="HS256")


def verify_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, AUTH_SECRET, algorithms=["HS256"])
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not AUTH_ENABLED:
        return await call_next(request)

    public_paths = {"/login", "/api/auth/login", "/health", "/static/login.html", "/static/style.css"}
    if request.url.path in public_paths or request.url.path.startswith("/static/"):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        username = verify_token(auth_header[7:])
        if username:
            return await call_next(request)

    if request.url.path == "/":
        return RedirectResponse(url="/login")

    raise HTTPException(status_code=401, detail="Unauthorized")


# ── Auth endpoints ────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(req: LoginRequest):
    if not AUTH_ENABLED:
        return {"token": "", "username": req.username}
    if req.username != AUTH_USER or req.password != AUTH_PASS:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(req.username)
    return {"token": token, "username": req.username}


@app.get("/login")
def login_page():
    path = os.path.join(static_dir, "login.html")
    if os.path.isfile(path):
        return HTMLResponse(open(path).read())
    return HTMLResponse("<h1>Login page not found</h1>", status_code=404)


# ── Frontend SPA ──────────────────────────────────────────────────────

@app.get("/")
def index():
    html = open(os.path.join(static_dir, "index.html")).read()
    if AUTH_ENABLED:
        # Inject auth config — frontend will redirect to /login if no token
        pass
    return HTMLResponse(html)


# ── Job Manager ───────────────────────────────────────────────────────

job_manager = JobManager()
set_manager(job_manager)


# ── Models ────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    prompt: str
    commit_message: Optional[str] = None
    jenkins_parameters: Optional[dict[str, str]] = None
    skip_git: bool = False
    skip_jenkins: bool = False


class GenerateResponse(BaseModel):
    job_id: str
    status: str
    status_url: str


class JobResponse(BaseModel):
    job_id: str
    status: str
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    pending_question: Optional[str] = None
    log: Optional[str] = None


class InputRequest(BaseModel):
    answer: str


class TransitionRequest(BaseModel):
    status: str
    error: Optional[str] = None


VALID_TRANSITIONS = {
    "queued": ["running", "failed", "cancelled"],
    "running": ["completed", "failed", "cancelled", "awaiting_input"],
    "awaiting_input": ["running", "failed", "cancelled", "completed"],
    "completed": ["failed"],
    "failed": ["queued", "running"],
    "cancelled": ["queued"],
}


# ── API Endpoints ─────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "version": app.version}


@app.get("/api/jobs")
def list_jobs_api(limit: int = 50):
    return {"jobs": job_manager.list_jobs(limit=limit)}


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs/{job_id}/log")
def get_job_log(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    result = job.get("result")
    conv_log = None
    if result and isinstance(result, dict):
        conv_log = result.get("conversation_log")
    if conv_log is None and job.get("log"):
        try:
            conv_log = jmod.loads(job["log"])
        except Exception:
            conv_log = None
    return {"log": conv_log or []}


@app.get("/api/jobs/{job_id}/events")
async def stream_events(job_id: str, index: int = 0):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    from app.job_manager import get_events_since, _job_events

    async def event_stream():
        seen = index
        terminal = {"completed", "failed", "cancelled"}
        while True:
            if job_id not in _job_events:
                await asyncio.sleep(0.5)
                continue
            entries = get_events_since(job_id, seen)
            for e in entries:
                seen += 1
                yield f"data: {jmod.dumps(e)}\n\n"
            current = job_manager.get_job(job_id)
            if current and current.get("status") in terminal:
                yield "data: {\"type\":\"done\"}\n\n"
                break
            await asyncio.sleep(0.3)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    ok = job_manager.cancel_job(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Job cannot be cancelled")
    return {"status": "cancelled"}


@app.post("/api/jobs/{job_id}/transition")
def transition_job(job_id: str, req: TransitionRequest):
    from app.db import upsert_job
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    current = job.get("status", "")
    allowed = VALID_TRANSITIONS.get(current, [])
    if req.status not in allowed:
        raise HTTPException(status_code=400, detail=f"Cannot transition from '{current}' to '{req.status}'")
    import datetime
    fields = {"status": req.status}
    if req.status == "running":
        fields["started_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if req.status in ("completed", "failed", "cancelled"):
        fields["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if req.error:
        fields["error"] = req.error
    upsert_job(job_id, **fields)
    return {"status": req.status}


@app.post("/api/generate", status_code=202, response_model=GenerateResponse)
def generate(req: GenerateRequest):
    log.info("create job prompt=%.60s", req.prompt)
    try:
        config.validate()
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    job_id = job_manager.create_job(
        func=run_pipeline, config=config, prompt=req.prompt,
        commit_message=req.commit_message, jenkins_parameters=req.jenkins_parameters,
        skip_git=req.skip_git, skip_jenkins=req.skip_jenkins,
    )
    return GenerateResponse(job_id=job_id, status="queued", status_url=f"/api/jobs/{job_id}")


@app.post("/api/jobs/{job_id}/input")
def submit_input(job_id: str, req: InputRequest):
    ok = job_manager.submit_input(job_id, req.answer)
    if not ok:
        raise HTTPException(status_code=400, detail="Job is not waiting for input")
    return {"status": "ok"}


# ── Entry point ───────────────────────────────────────────────────────

def main():
    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", "8000"))
    reload = os.getenv("SERVER_RELOAD", "").lower() == "true"
    log.info("listening on %s:%s auth=%s", host, port, AUTH_ENABLED)
    uvicorn.run("app.server:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
