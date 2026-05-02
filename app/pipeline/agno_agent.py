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


def _run_cmd(cmd: list[str], cwd: str) -> str:
    import subprocess
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
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
    return get_jm().pause_for_input(_current_job_id(), question)


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

def clone_repo_step(step_input: StepInput) -> StepOutput:
    """Step 1: Clone or pull the target GitHub repository."""
    remote_url = step_input.additional_data.get("remote_url", "")
    branch = step_input.additional_data.get("branch", "main")

    if not remote_url:
        return StepOutput(content="No remote URL provided, skipping clone.")

    cwd = os.path.abspath(REPO_DIR)
    git_dir = os.path.join(cwd, ".git")

    if os.path.isdir(git_dir):
        _run_cmd(["git", "checkout", branch], cwd)
        _run_cmd(["git", "pull", "origin", branch], cwd)
        msg = f"Pulled latest from {remote_url}/{branch}"
    else:
        if os.path.isdir(cwd):
            shutil.rmtree(cwd)
        _run_cmd(
            ["git", "clone", "--branch", branch, remote_url, REPO_DIR],
            os.path.dirname(os.path.abspath(REPO_DIR)),
        )
        msg = f"Cloned {remote_url} (branch: {branch})"

    return StepOutput(content=msg, additional_data={"clone_status": "ok"})


def publish_step(step_input: StepInput) -> StepOutput:
    """Step 3: Commit, push to GitHub, and trigger Jenkins."""
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
            if status:
                commit_msg = data.get("commit_message", "infra: Terraform update")
                branch = data.get("branch", "main")
                _run_cmd(["git", "commit", "-m", commit_msg], cwd)
                _run_cmd(["git", "push", "-u", "origin", branch], cwd)
                logs.append(f"Git: committed and pushed to {branch}")
            else:
                logs.append("Git: no changes to commit")
        except RuntimeError as e:
            logs.append(f"Git error: {e}")
    else:
        logs.append("Git: no remote URL configured, skipped")

    # Jenkins trigger
    if data.get("skip_jenkins"):
        logs.append("Jenkins: skipped")
    elif data.get("jenkins_url"):
        import requests
        from requests.auth import HTTPBasicAuth

        job_url = f"{data['jenkins_url'].rstrip('/')}/job/{data['jenkins_job_name']}/buildWithParameters"
        auth = HTTPBasicAuth(data.get("jenkins_user", ""), data.get("jenkins_api_token", ""))

        try:
            resp = requests.post(job_url, auth=auth, timeout=30)
            if resp.status_code == 201:
                location = resp.headers.get("Location", "unknown")
                logs.append(f"Jenkins: triggered, queue: {location}")
            else:
                logs.append(f"Jenkins error: HTTP {resp.status_code}")
        except Exception as e:
            logs.append(f"Jenkins error: {e}")
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
        system_message_role="system",
        system_message=f"""You are a senior infrastructure engineer specializing in Terraform.

The Git repository has already been cloned. Your job is ONLY to:
1. Read the user's infrastructure request
2. Generate Terraform code
3. Write it to files using write_terraform_file

Do NOT ask about GitHub URLs or Jenkins — those are handled automatically.

Rules:
- Use Terraform >= 1.5 syntax with `required_providers` blocks
- Write provider config, variables, resources, and outputs using write_terraform_file
- One file per concern: provider.tf, variables.tf, <resource>.tf, outputs.tf
- Use variables for anything configurable (region, names, tags)
- Default to AWS provider, region us-east-1 unless specified otherwise
- Include security best practices: encryption, public access blocks
- Tag all resources with Name, Environment, ManagedBy
- NEVER use placeholder values like "CHANGEME"

If you need more details (bucket name, region, instance type, etc.),
call the ask_user tool and wait for the response before writing files.

{_load_skills()}""",
        markdown=True,
        add_history_to_context=True,
        num_history_runs=1,
    )


def _build_db():
    from agno.db.sqlite import SqliteDb
    return SqliteDb(db_file="tmp/infra_agent.db")


def _make_terraform_step(config: Config):
    """Factory: returns a function step that runs the Terraform agent."""

    def terraform_step(step_input: StepInput) -> StepOutput:
        agent = _build_terraform_agent(config)
        result = agent.run(input=step_input.input)
        content = result.content if hasattr(result, "content") else str(result)
        return StepOutput(content=content)

    terraform_step.__name__ = "terraform_step"
    return terraform_step


def _build_workflow(config: Config) -> Workflow:
    return Workflow(
        name="Infra Pipeline",
        description="Clone repo → Generate Terraform → Publish & Jenkins",
        db=_build_db(),
        steps=[
            clone_repo_step,
            _make_terraform_step(config),
            publish_step,
        ],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(config: Config, prompt: str, job_id: Optional[str] = None,
        skip_git: bool = False, skip_jenkins: bool = False) -> dict:
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
        "remote_url": config.git_remote_url,
        "branch": config.git_branch,
        "commit_message": prompt,
        "jenkins_url": config.jenkins_url,
        "jenkins_job_name": config.jenkins_job_name,
        "jenkins_user": config.jenkins_user,
        "jenkins_api_token": config.jenkins_api_token,
        "skip_git": skip_git,
        "skip_jenkins": skip_jenkins,
    }

    workflow = _build_workflow(config)
    result = workflow.run(input=enriched, additional_data=additional_data)

    response_parts = []
    if hasattr(result, "events") and result.events:
        for event in result.events:
            if hasattr(event, "content") and event.content:
                response_parts.append(str(event.content))

    files = []
    if os.path.isdir(REPO_DIR):
        files = sorted(
            os.path.join(REPO_DIR, f)
            for f in os.listdir(REPO_DIR)
            if f.endswith(".tf")
        )

    return {
        "response": "\n\n".join(response_parts) if response_parts else (
            result.content if hasattr(result, "content") else str(result)
        ),
        "repo_dir": REPO_DIR,
        "files": files,
    }
