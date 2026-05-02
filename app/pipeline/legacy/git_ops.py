import subprocess
import os
from app.config import Config


class GitError(Exception):
    pass


def _run(cmd: list[str], cwd: str) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise GitError(f"Command {' '.join(cmd)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def run(config: Config, terraform_dir: str, commit_message: str) -> dict:
    if not config.git_remote_url:
        return {"status": "skipped", "reason": "GIT_REMOTE_URL not set"}

    cwd = os.path.abspath(terraform_dir)
    logs = []

    git_dir = os.path.join(cwd, ".git")
    if not os.path.isdir(git_dir):
        _run(["git", "init"], cwd)
        _run(["git", "checkout", "-b", config.git_branch], cwd)
        logs.append(f"Initialized git repo on branch '{config.git_branch}'")

    remotes = _run(["git", "remote"], cwd)
    if "origin" not in remotes:
        _run(["git", "remote", "add", "origin", config.git_remote_url], cwd)
        logs.append(f"Added remote origin: {config.git_remote_url}")

    _run(["git", "add", "-A"], cwd)
    status = _run(["git", "status", "--porcelain"], cwd)
    if not status:
        logs.append("No changes to commit")
        return {"status": "no_changes", "logs": logs}
    _run(["git", "commit", "-m", commit_message], cwd)
    logs.append(f"Committed: {commit_message}")

    _run(["git", "push", "-u", "origin", config.git_branch], cwd)
    logs.append("Pushed to GitHub successfully.")

    return {"status": "success", "logs": logs, "branch": config.git_branch}
