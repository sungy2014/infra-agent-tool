#!/usr/bin/env python3
import os
import sys
import uuid
import logging
import warnings
from typing import Optional
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

warnings.filterwarnings("ignore", message=".*OpenSSL.*")

from app.config import Config
from app.pipeline.core import run_pipeline
from app.job_manager import JobManager, set_manager
from app.db import list_jobs, delete_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("infra-agent")

config = Config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting infra-agent server")
    yield
    log.info("shutting down — in-flight jobs will be terminated")


app = FastAPI(title="Infra Agent API", version="1.0.0", lifespan=lifespan)

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

API_KEY = os.getenv("INFRA_AGENT_API_KEY", "")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not API_KEY:
        return await call_next(request)
    if request.url.path in ("/health", "/", "/docs", "/openapi.json"):
        return await call_next(request)
    if request.url.path.startswith("/static/"):
        return await call_next(request)
    header = request.headers.get("Authorization", "")
    if header != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return await call_next(request)


@app.get("/")
def index():
    from fastapi.responses import HTMLResponse
    html = open(os.path.join(static_dir, "index.html")).read()
    if API_KEY:
        html = html.replace("</head>", f'<meta name="api-key" content="{API_KEY}"></head>')
    return HTMLResponse(html)


job_manager = JobManager()
set_manager(job_manager)


# --- Request / Response models ---

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


class InputRequest(BaseModel):
    answer: str


# --- Endpoints ---

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


@app.post("/api/generate", status_code=202, response_model=GenerateResponse)
def generate(req: GenerateRequest):
    log.info("create job prompt=%.60s", req.prompt)
    try:
        config.validate()
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    job_id = job_manager.create_job(
        func=run_pipeline,
        config=config,
        prompt=req.prompt,
        commit_message=req.commit_message,
        jenkins_parameters=req.jenkins_parameters,
        skip_git=req.skip_git,
        skip_jenkins=req.skip_jenkins,
    )
    return GenerateResponse(
        job_id=job_id,
        status="queued",
        status_url=f"/api/jobs/{job_id}",
    )


@app.post("/api/jobs/{job_id}/input")
def submit_input(job_id: str, req: InputRequest):
    ok = job_manager.submit_input(job_id, req.answer)
    if not ok:
        raise HTTPException(status_code=400, detail="Job is not waiting for input")
    return {"status": "ok"}


# --- Entry point ---

def main():
    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", "8000"))
    reload = os.getenv("SERVER_RELOAD", "").lower() == "true"
    log.info("listening on %s:%s", host, port)
    uvicorn.run("app.server:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
