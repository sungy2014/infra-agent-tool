import os
import re
import json
import shutil
import pathlib
import threading
from typing import Optional

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools.decorator import tool
from agno.workflow import Workflow
from agno.workflow.types import StepInput, StepOutput

from app.config import Config

# Monkey-patch agno's _format_message to include reasoning_content for DeepSeek
_original_format_message = OpenAIChat._format_message


def _patched_format_message(self, message, compress_tool_results=False):
    msg = _original_format_message(self, message, compress_tool_results)
    if msg["role"] == "assistant" and getattr(message, "reasoning_content", None):
        msg["reasoning_content"] = message.reasoning_content
    return msg


OpenAIChat._format_message = _patched_format_message

REPO_DIR = "repo"


def _ensure_dir(path: str):
    pathlib.Path(path).mkdir(parents=True, exist_ok=True)


def _run_cmd(cmd: list[str], cwd: str, timeout: int = 120) -> str:
    import subprocess
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Shared tools
# ---------------------------------------------------------------------------

_job_id_per_thread: dict[int, str] = {}


def _current_job_id() -> str:
    return _job_id_per_thread.get(threading.current_thread().ident, "")


@tool
def ask_user(question: str) -> str:
    """Ask the user for additional information needed to proceed.

    Args:
        question: The question to ask the user
    """
    from app.job_manager import get_manager as get_jm
    job_id = _current_job_id()
    answer = get_jm().pause_for_input(job_id, question)
    if job_id:
        _user_answers.setdefault(job_id, []).append(answer)
    return answer


@tool(show_result=True)
def write_terraform_file(filename: str, content: str) -> str:
    """Write a Terraform file into the repo directory.

    Args:
        filename: The name of the file (e.g. main.tf, network.tf)
        content: The Terraform HCL content to write
    """
    _ensure_dir(REPO_DIR)
    sanitized = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
    if not sanitized.endswith(".tf"):
        sanitized += ".tf"
    filepath = os.path.join(REPO_DIR, sanitized)
    with open(filepath, "w") as f:
        f.write(content)
    return f"Written: {filepath}"


# ---------------------------------------------------------------------------
# Workflow steps
# ---------------------------------------------------------------------------

def _check_cancelled():
    job_id = _current_job_id()
    if job_id:
        from app.job_manager import get_manager as get_jm
        if get_jm().is_cancelled(job_id):
            raise RuntimeError("Job was cancelled")


def clone_repo_step(step_input: StepInput) -> StepOutput:
    """Step 1: Clone or pull the target GitHub repository."""
    _check_cancelled()
    data = step_input.additional_data
    remote_url = data.get("remote_url", "")
    branch = data.get("branch", "main")

    if data.get("skip_git"):
        return StepOutput(content="Git: skipped")
    if not remote_url:
        return StepOutput(content="No remote URL provided, skipping clone.")

    cwd = os.path.abspath(REPO_DIR)
    git_dir = os.path.join(cwd, ".git")

    if os.path.isdir(git_dir):
        _run_cmd(["git", "checkout", branch], cwd)
        _check_cancelled()
        _run_cmd(["git", "pull", "origin", branch], cwd)
        msg = f"Pulled latest from {remote_url}/{branch}"
    else:
        # Ensure repo directory exists (may not exist outside Docker)
        os.makedirs(cwd, exist_ok=True)
        # Remove contents (not the dir itself) so we can clone into it.
        if os.path.isdir(cwd):
            for entry in os.listdir(cwd):
                entry_path = os.path.join(cwd, entry)
                if os.path.isfile(entry_path) or os.path.islink(entry_path):
                    os.remove(entry_path)
                elif os.path.isdir(entry_path):
                    shutil.rmtree(entry_path)
        _run_cmd(
            ["git", "clone", "--branch", branch, remote_url, "."],
            cwd,
        )
        msg = f"Cloned {remote_url} (branch: {branch})"

    _check_cancelled()
    return StepOutput(content=msg)


def publish_step(step_input: StepInput) -> StepOutput:
    """Step 3: Commit, push to GitHub, and trigger Jenkins."""
    _check_cancelled()
    data = step_input.additional_data
    logs = []

    # Git commit and push
    if data.get("skip_git"):
        logs.append("Git: skipped")
    elif data.get("remote_url"):
        cwd = os.path.abspath(REPO_DIR)
        try:
            _run_cmd(["git", "add", "-A"], cwd)
            status = _run_cmd(["git", "status", "--porcelain"], cwd)
            _check_cancelled()
            if status:
                commit_msg = data.get("commit_message") or "infra: Terraform update"
                branch = data.get("branch", "main")
                _run_cmd(["git", "commit", "-m", commit_msg], cwd)
                commit_id = _run_cmd(["git", "rev-parse", "--short", "HEAD"], cwd)
                _run_cmd(["git", "push", "-u", "origin", branch], cwd)
                logs.append(f"Git: committed and pushed to {branch}")
                from app.job_manager import emit_event
                emit_event(data.get("job_id", ""), "commit", {
                    "hash": commit_id,
                    "message": commit_msg,
                    "branch": branch,
                    "url": f"{data.get('remote_url', '').rstrip('.git')}/commit/{commit_id}",
                })
            else:
                logs.append("Git: no changes to commit")
        except RuntimeError as e:
            logs.append(f"Git error: {e}")
    else:
        logs.append("Git: no remote URL configured, skipped")

    _check_cancelled()

    # Jenkins trigger + track
    if data.get("skip_jenkins"):
        logs.append("Jenkins: skipped")
    elif data.get("jenkins_url"):
        import requests, time
        from requests.auth import HTTPBasicAuth

        auth = HTTPBasicAuth(data.get("jenkins_user", ""), data.get("jenkins_api_token", ""))
        base = data["jenkins_url"].rstrip("/")
        job_name = data["jenkins_job_name"]
        job_url = f"{base}/job/{job_name}/buildWithParameters"

        try:
            # 1. Trigger
            resp = requests.post(job_url, auth=auth, timeout=30, params=data.get("jenkins_parameters"))
            if resp.status_code != 201:
                logs.append(f"Jenkins error: HTTP {resp.status_code}")
                raise RuntimeError(f"Jenkins trigger failed: {resp.status_code}")

            # 2. Parse queue location to get build number
            queue_url = resp.headers.get("Location", "")
            build_number = None
            for _ in range(30):  # wait up to 30s for queue
                _check_cancelled()
                q_resp = requests.get(f"{queue_url}api/json", auth=auth, timeout=10)
                if q_resp.status_code == 200:
                    q_data = q_resp.json()
                    executable = q_data.get("executable")
                    if executable:
                        build_number = executable.get("number")
                        break
                time.sleep(1)

            if not build_number:
                logs.append("Jenkins: triggered but build not found in queue")
                raise RuntimeError("Build was not assigned from queue")

            logs.append(f"Jenkins: build #{build_number} started")

            # 3. Poll build status
            build_url = f"{base}/job/{job_name}/{build_number}"
            result = None
            for _ in range(120):  # wait up to 120s
                _check_cancelled()
                b_resp = requests.get(f"{build_url}/api/json", auth=auth, timeout=10)
                if b_resp.status_code == 200:
                    b_data = b_resp.json()
                    if b_data.get("building") is False:
                        result = b_data.get("result")
                        break
                time.sleep(1)

            # 4. Fetch console output (last 80 lines)
            console = ""
            try:
                c_resp = requests.get(f"{build_url}/consoleText", auth=auth, timeout=15)
                if c_resp.status_code == 200:
                    lines = c_resp.text.strip().split("\n")
                    console = "\n".join(lines[-80:])
            except Exception:
                console = "(console fetch failed)"

            status = "SUCCESS" if result == "SUCCESS" else f"FAILED ({result})"
            logs.append(f"Jenkins: build #{build_number} {status}")

            from app.job_manager import emit_event
            emit_event(data.get("job_id", ""), "jenkins_build", {
                "build_number": build_number,
                "result": result or "UNKNOWN",
                "console": console[:2000],
                "url": build_url,
            })
        except Exception as e:
            logs.append(f"Jenkins error: {e}")
            emit_event(data.get("job_id", ""), "message", {
                "role": "system", "content": f"Jenkins error: {e}"
            })
    else:
        logs.append("Jenkins: no URL configured, skipped")

    return StepOutput(content="\n".join(logs))


# ---------------------------------------------------------------------------
# Skill loading
# ---------------------------------------------------------------------------

SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "skills")


def _load_skill(name: str) -> str:
    path = os.path.join(SKILLS_DIR, name, "SKILL.md")
    if not os.path.isfile(path):
        return ""
    with open(path) as f:
        lines = f.readlines()
    content = []
    in_frontmatter = False
    for line in lines:
        if line.strip() == "---":
            in_frontmatter = not in_frontmatter
            continue
        if not in_frontmatter:
            content.append(line)
    return "".join(content).strip()


def _load_skills() -> str:
    if not os.path.isdir(SKILLS_DIR):
        return ""
    parts = []
    for skill_name in sorted(os.listdir(SKILLS_DIR)):
        if skill_name.startswith(".") or not os.path.isdir(os.path.join(SKILLS_DIR, skill_name)):
            continue
        body = _load_skill(skill_name)
        if body:
            parts.append(body)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Model / Agent / Workflow factory
# ---------------------------------------------------------------------------

def _build_model(config: Config) -> OpenAIChat:
    kwargs = {
        "id": config.llm_model,
        "api_key": config.llm_api_key,
        "temperature": 0.3,
        "role_map": {"system": "system", "user": "user", "assistant": "assistant", "tool": "tool", "model": "assistant"},
    }
    if config.llm_reasoning_effort:
        kwargs["reasoning_effort"] = config.llm_reasoning_effort
    if config.llm_base_url:
        kwargs["base_url"] = config.llm_base_url
    elif config.llm_provider == "deepseek":
        kwargs["base_url"] = "https://api.deepseek.com"
    return OpenAIChat(**kwargs)


def _build_terraform_agent(config: Config) -> Agent:
    model = _build_model(config)
    return Agent(
        name="Terraform Generator",
        model=model,
        tools=[ask_user, write_terraform_file],
        db=_build_db(),
        system_message_role="system",
        system_message=f"""You are a senior infrastructure engineer specializing in Terraform.

You are given a complete infrastructure request. Generate and write the Terraform code immediately.

Your job is ONLY to:
1. Parse the user's request
2. Generate Terraform code using sensible defaults for anything not specified
3. Write files using write_terraform_file

Do NOT ask the user for missing details — use defaults:
- Region: us-east-1
- Environment: production  
- Tags: Name, Environment, ManagedBy

Do NOT ask about GitHub URLs or Jenkins — those are handled by other steps.

Rules:
- Use Terraform >= 1.5 syntax with `required_providers` blocks
- Write provider config, variables, resources, and outputs using write_terraform_file
- One file per concern: provider.tf, variables.tf, <resource>.tf, outputs.tf
- Use variables for things that should be configurable
- Default to AWS provider ~> 5.0
- Include security best practices: encryption, public access blocks
- NEVER use placeholder values like "CHANGEME"

Only call ask_user if the user's request is truly ambiguous (e.g. they ask "create something" without specifying any resource type).

{_load_skills()}""",
        markdown=True,
        add_history_to_context=True,
        num_history_runs=1,
    )


def _build_db():
    from agno.db.postgres import PostgresDb
    db_url = os.environ.get("DATABASE_URL", "postgresql://infra:infra@localhost:5432/infra")
    db = PostgresDb(db_url=db_url)
    _ = db.create_schema  # property — triggers table creation
    return db


_conversation_logs: dict[str, list[dict]] = {}
_agent_results: dict[str, dict] = {}
_user_answers: dict[str, list[str]] = {}


def _make_terraform_step(config: Config):
    """Factory: returns a function step that runs the Terraform agent."""

    def terraform_step(step_input: StepInput) -> StepOutput:
        _check_cancelled()
        agent = _build_terraform_agent(config)
        result = agent.run(input=step_input.input)

        log_entries = []
        answer_idx = 0
        user_ans = _user_answers.get(_current_job_id() or "", [])
        try:
            msgs = getattr(result, "messages", None) or []
            for m in msgs:
                role = getattr(m, "role", "unknown")
                content = getattr(m, "content", "") or ""
                tc = getattr(m, "tool_calls", None)
                rc = getattr(m, "reasoning_content", None)
                entry = {"role": role, "content": str(content)[:500]}
                if rc:
                    entry["reasoning"] = str(rc)[:300]
                if tc:
                    parsed = []
                    for t in tc:
                        fn = t.get("function", {}) if isinstance(t, dict) else getattr(t, "function", {})
                        name = fn.get("name", "")
                        if name == "ask_user" and answer_idx < len(user_ans):
                            entry["user_answer"] = user_ans[answer_idx]
                            answer_idx += 1
                        parsed.append({"name": name, "args": str(fn.get("arguments", ""))[:80]})
                    entry["tool_calls"] = parsed
                log_entries.append(entry)
        except Exception as exc:
            log_entries.append({"role": "system", "content": f"log error: {exc}"})

        job_id = _current_job_id()
        if job_id and log_entries:
            from app.db import upsert_job
            try:
                upsert_job(job_id, log=json.dumps(log_entries))
                _conversation_logs[job_id] = log_entries
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(
    config: Config, prompt: str, job_id: Optional[str] = None,
    commit_message: Optional[str] = None,
    jenkins_parameters: Optional[dict[str, str]] = None,
    skip_git: bool = False, skip_jenkins: bool = False,
) -> dict:
    _job_id_per_thread[threading.current_thread().ident] = job_id or ""

    repo_abs = os.path.abspath(REPO_DIR)
    if os.path.isdir(repo_abs):
        for entry in os.listdir(repo_abs):
            entry_path = os.path.join(repo_abs, entry)
            if os.path.isfile(entry_path) or os.path.islink(entry_path):
                os.remove(entry_path)
            elif os.path.isdir(entry_path) and entry != ".git":
                shutil.rmtree(entry_path)

    # Enrich the prompt with environment configuration
    enriched = prompt
    if config.git_remote_url:
        enriched += f"\n\nGit repository URL: {config.git_remote_url}\nGit branch: {config.git_branch}"
    if not skip_jenkins and config.jenkins_url:
        enriched += (
            f"\n\nJenkins job to trigger: {config.jenkins_job_name}\n"
            f"Jenkins URL: {config.jenkins_url}\n"
            f"Jenkins user: {config.jenkins_user}"
        )

    additional_data = {
        "job_id": job_id or "",
        "remote_url": config.git_remote_url,
        "branch": config.git_branch,
        "commit_message": commit_message or prompt,
        "jenkins_url": config.jenkins_url,
        "jenkins_job_name": config.jenkins_job_name,
        "jenkins_user": config.jenkins_user,
        "jenkins_api_token": config.jenkins_api_token,
        "jenkins_parameters": jenkins_parameters,
        "skip_git": skip_git,
        "skip_jenkins": skip_jenkins,
    }

    # Check for cancellation before starting
    from app.job_manager import get_manager as get_jm, emit_event
    if get_jm().is_cancelled(job_id or ""):
        return {"response": "Job was cancelled before execution", "repo_dir": REPO_DIR, "files": []}

    emit_event(job_id, "step", {"label": "Cloning repository"})

    # Step 1: Clone repo
    step1_log = ""
    if config.git_remote_url and not skip_git:
        try:
            step1_result = clone_repo_step(StepInput(
                input=enriched,
                additional_data=additional_data,
            ))
            step1_log = step1_result.content
            emit_event(job_id, "step_done", {"label": "Clone", "detail": step1_log[:100]})
        except Exception as e:
            step1_log = f"Clone error: {e}"
            emit_event(job_id, "step_error", {"label": "Clone", "error": str(e)})

    emit_event(job_id, "step", {"label": "Generating Terraform code"})

    # Step 2: Run the Terraform agent directly
    agent = _build_terraform_agent(config)
    agent_result = agent.run(input=enriched)
    agent_content = agent_result.content if hasattr(agent_result, "content") else str(agent_result)

    # Capture and emit conversation log at once
    conv_log = []
    try:
        msgs = getattr(agent_result, "messages", None) or []
        for m in msgs:
            role = getattr(m, "role", "unknown")
            content = str(getattr(m, "content", "") or "")[:500]
            rc = getattr(m, "reasoning_content", None)
            tc = getattr(m, "tool_calls", None)
            entry = {"role": role, "content": content}
            if rc:
                entry["reasoning"] = str(rc)[:300]
            if tc:
                parsed = []
                for t in tc:
                    fn = t.get("function", {}) if isinstance(t, dict) else getattr(t, "function", {})
                    name = fn.get("name", "")
                    parsed.append({"name": name, "args": str(fn.get("arguments", ""))[:80]})
                entry["tool_calls"] = parsed
            conv_log.append(entry)
            emit_event(job_id, "message", entry)
        if job_id:
            from app.db import upsert_job
            try:
                upsert_job(job_id, log=json.dumps(conv_log))
            except Exception:
                pass
    except Exception as exc:
        conv_log.append({"role": "system", "content": f"log error: {exc}"})

    emit_event(job_id, "step_done", {"label": "Terraform generation"})

    # Approval gate: pause and ask for human review before publishing
    user_approved = True  # default: approve when nothing to publish
    if not skip_git and not skip_jenkins:
        if config.git_remote_url or config.jenkins_url:
            files = []
            if os.path.isdir(REPO_DIR):
                files = sorted(
                    os.path.join(REPO_DIR, f)
                    for f in os.listdir(REPO_DIR)
                    if f.endswith(".tf")
                )
            approval_msg = (
                f"The agent has generated {len(files)} Terraform file(s):\n"
                + "\n".join(f"  • {f}" for f in files)
                + f"\n\nThis will be committed to {config.git_remote_url or '(no git remote)'}"
                + (f" and applied by Jenkins job '{config.jenkins_job_name}'" if config.jenkins_url else "")
                + "\n\nReply with: approve  or  reject"
            )
            emit_event(job_id, "approval_required", {
                "summary": approval_msg,
                "files": files,
            })
            from app.job_manager import get_manager as get_jm2
            answer = get_jm2().pause_for_input(job_id or "", approval_msg)
            user_approved = "approve" in answer.lower() and "reject" not in answer.lower()
            if user_approved:
                emit_event(job_id, "step", {"label": "Approved — Publishing"})
            else:
                emit_event(job_id, "step_error", {"label": "Rejected by user", "error": "User rejected the changes"})
                step3_log = "Rejected by user"

    # Step 3: Publish (git push + Jenkins) — only if approved
    if user_approved:
        step3_log = ""
        if not skip_git or not skip_jenkins:
            emit_event(job_id, "step", {"label": "Publishing (git + Jenkins)"})
            try:
                step3_result = publish_step(StepInput(
                    input=enriched,
                    additional_data=additional_data,
                ))
                step3_log = step3_result.content
                emit_event(job_id, "step_done", {"label": "Publish", "detail": step3_log[:100]})
            except Exception as e:
                step3_log = f"Publish error: {e}"
                emit_event(job_id, "step_error", {"label": "Publish", "error": str(e)})

    emit_event(job_id, "complete", {})

    files = []
    if os.path.isdir(REPO_DIR):
        files = sorted(
            os.path.join(REPO_DIR, f)
            for f in os.listdir(REPO_DIR)
            if f.endswith(".tf")
        )

    return {
        "response": "\n".join(filter(None, [step1_log, agent_content, step3_log])),
        "repo_dir": REPO_DIR,
        "files": files,
        "conversation_log": conv_log,
    }
