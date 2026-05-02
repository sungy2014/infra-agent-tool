#!/usr/bin/env python3
import os
import warnings
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

warnings.filterwarnings("ignore", message=".*OpenSSL.*")

from app.config import Config
from app.pipeline.core import run_pipeline
from app.job_manager import JobManager, set_manager

app = FastAPI(title="Infra Agent API", version="1.0.0")

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def index():
    from fastapi.responses import FileResponse
    return FileResponse(os.path.join(static_dir, "index.html"))


job_manager = JobManager()
set_manager(job_manager)


# --- Request / Response models ---

class GenerateRequest(BaseModel):
    prompt: str
    commit_message: Optional[str] = None
    jenkins_parameters: Optional[dict[str, str]] = None
    skip_git: bool = False
    skip_jenkins: bool = False
    use_agno: bool = False


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
    return {"status": "ok"}


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": job_manager.list_jobs()}


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/generate", status_code=202, response_model=GenerateResponse)
def generate(req: GenerateRequest):
    config = Config()
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
        use_agno=req.use_agno,
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
    config = Config()
    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", "8000"))
    reload = os.getenv("SERVER_RELOAD", "").lower() == "true"
    uvicorn.run("app.server:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
