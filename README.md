# Infra Agent Tool

AI-powered infrastructure management agent. Describe infrastructure in natural language — it generates Terraform code, pushes to GitHub, and triggers a Jenkins pipeline.

```
User prompt ──► Infra Agent API ──► Clone repo ──► Generate Terraform ──► Commit & push ──► Jenkins apply
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           FastAPI Server (:8000)                        │
│                                                                         │
│  ┌──────────────┐    ┌──────────────────┐    ┌────────────────────────┐  │
│  │  REST API    │    │   JobManager     │    │  PostgreSQL (persist)  │  │
│  │  /api/*      │───►│   async queue    │───►│  infra_agent db        │  │
│  │  /static/*   │    │   thread pool    │    │  - jobs table          │  │
│  └──────────────┘    └────────┬─────────┘    └────────────────────────┘  │
│                               │                                         │
│                    ┌──────────▼──────────────┐                          │
│                    ┌─────────────────────────┐                          │
│                    │   Pipeline Router       │                          │
│                    │   (pipeline/core.py)    │                          │
│                    │   runs Agno Workflow    │                          │
│                    └────────────┬────────────┘                          │
│                                 │                                        │
└─────────────────────────────────┼────────────────────────────────────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │  Agno Workflow              │
                    │                             │
                    │  Step 1: Clone repo         │
                    │  (function)                 │
                    │                             │
                    │  Step 2: Generate Terraform │
                    │  (Agent + LLM)              │
                    │                             │
                    │  Step 3: Publish            │
                    │  (function)                 │
                    └─────────────────────────────┘
```

---

## System Components

### API Layer (`app/server.py`)

FastAPI server with five endpoints:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Frontend SPA |
| `GET` | `/health` | Liveness check |
| `GET` | `/api/jobs` | List job history |
| `GET` | `/api/jobs/{id}` | Poll job status & result |
| `POST` | `/api/generate` | Submit infrastructure request |
| `POST` | `/api/jobs/{id}/input` | Submit answer for interactive prompt |

### Job Manager (`app/job_manager.py`)

Async background job execution with interactive input support:

```
queued ──► running ──► completed
              │
              ├──► awaiting_input ──► running ──► completed
              │                          │
              └──► failed                └──► failed
```

- Each job runs in a daemon thread
- Interactive prompts use `threading.Event` to block/resume
- All state persisted to PostgreSQL via `app/db.py`

### Storage (`app/db.py`)

- **PostgreSQL** via `psycopg2` with thread-local connections
- Single `jobs` table: `job_id`, `status`, timestamps, `result` (JSON), `error`, `pending_question`, `log`
- Agno's `PostgresDb` separately stores workflow session history

### Pipeline Router (`app/pipeline/core.py`)

Runs the Agno Workflow pipeline:

```
run_pipeline() ──► agno_agent.run() ──► Workflow.run()
```

---

## Agno Workflow Pipeline

The primary execution path uses `agno.Workflow` with three sequential steps:

```
┌──────────────────────────────────────────────────────────────────────┐
│                       Workflow("Infra Pipeline")                     │
│                                                                      │
│  additional_data = {remote_url, branch, commit_message,              │
│                     jenkins_url, jenkins_job_name, ...}              │
│                                                                      │
│  ┌──────────────────┐    ┌───────────────────┐    ┌───────────────┐  │
│  │   Step 1         │    │   Step 2          │    │   Step 3      │  │
│  │   clone_repo     │───►│   Terraform       │───►│   publish     │  │
│  │   (function)     │    │   Agent (LLM)     │    │   (function)  │  │
│  │                  │    │                   │    │               │  │
│  │   git clone      │    │   ask_user()      │    │   git add     │  │
│  │   or git pull    │    │   write_terraform │    │   git commit  │  │
│  │                  │    │   _file()         │    │   git push    │  │
│  └──────────────────┘    └───────────────────┘    │   POST Jenkins│  │
│                                                    └───────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

### Step Details

**Step 1 — `clone_repo_step`** (plain function, no LLM cost):
- Reads `remote_url` and `branch` from `additional_data`
- If `repo/.git` exists: `git checkout <branch> && git pull`
- Otherwise: `rm -rf repo && git clone --branch <branch> <url> repo`

**Step 2 — `Terraform Generator`** (Agno Agent with tools):
- **Tools**: `ask_user`, `write_terraform_file`
- **System prompt**: includes all loaded skill definitions from `skills/`
- Generates provider config, variables, resources, and outputs as `.tf` files
- Calls `ask_user` when user input is needed (pauses workflow)
- Writes files to `repo/` via `write_terraform_file`

**Step 3 — `publish_step`** (plain function, no LLM cost):
- `git add -A`, checks `git status --porcelain`, commits, pushes
- `POST /job/{name}/buildWithParameters` to Jenkins with HTTP Basic Auth
- Respects `skip_git` and `skip_jenkins` flags

### Tools

| Tool | Signature | Description |
|------|-----------|-------------|
| `ask_user` | `(question: str) -> str` | Pauses job, waits for user answer |
| `write_terraform_file` | `(filename: str, content: str) -> str` | Writes `.tf` to `repo/` |

### Skill Injection

All skill definitions from `skills/` are loaded and injected into the Terraform Agent's system prompt at agent build time:

```python
system_message=f"""...{_load_skills()}"""
```

---

## Interactive Input Flow

When the LLM needs more information, it calls the `ask_user` tool:

```
Agent                    JobManager                    API/Browser
  │                         │                            │
  │── pause_for_input() ───►│                            │
  │                         │── status="awaiting_input"  │
  │                         │── pending_question="..."   │
  │                         │                            │
  │                         │  ◄── POST /jobs/{id}/input │
  │                         │       {answer: "..."}      │
  │                         │                            │
  │  ◄── returns answer ────│                            │
  │                         │── status="running"         │
```

---

## Skills System

```
skills/
├── generate-terraform/SKILL.md        # TF generation rules
├── git-operations/SKILL.md            # Git commit conventions
├── jenkins-pipeline/SKILL.md          # Jenkins trigger rules
└── infra-pipeline/SKILL.md            # End-to-end orchestrator
```

Skills follow the [Agent Skills](https://agentskills.io) open standard with YAML frontmatter:

```yaml
---
name: generate-terraform
description: Generate Terraform code for AWS infrastructure
allowed-tools: Write *
---
```

Each skill defines: steps, rules, examples, and tool permissions. The agent loads all skills at build time.

---

## Configuration

All configuration via `.env` file at project root:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLM_PROVIDER` | No | `openai` | `openai` or `deepseek` |
| `LLM_API_KEY` | **Yes** | — | API key |
| `LLM_MODEL` | No | `gpt-4o` | Model ID |
| `LLM_BASE_URL` | No | — | Custom base URL |
| `LLM_REASONING_EFFORT` | No | — | `high`, `medium`, etc. |
| `GIT_REMOTE_URL` | No | — | GitHub remote |
| `GIT_BRANCH` | No | `main` | Git branch |
| `JENKINS_URL` | No | — | Jenkins server |
| `JENKINS_USER` | No | — | Jenkins username |
| `JENKINS_API_TOKEN` | No | — | Jenkins token |
| `JENKINS_JOB_NAME` | No | — | Jenkins job name |
| `SERVER_HOST` | No | `0.0.0.0` | Bind host |
| `SERVER_PORT` | No | `8000` | Bind port |
| `SERVER_RELOAD` | No | `false` | Hot reload |

---

## Frontend

Single-page application (vanilla JS, no framework) at `app/static/`:

- **Sidebar**: Server health indicator, job history list (auto-refreshes)
- **Main form**: Prompt input, skip/agno checkboxes
- **Job detail**: Status badge, response text, file list, 4-step timeline, interactive input

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your LLM_API_KEY and Jenkins/GitHub settings

# Start the server
python3 server.py

# Open the UI
open http://localhost:8000

# Or use the CLI
python3 main.py generate "create an S3 bucket" --use-agno
```

## Docker (Jenkins)

```bash
docker compose up -d
# Jenkins at http://localhost:8080 (admin / admin123)
```
