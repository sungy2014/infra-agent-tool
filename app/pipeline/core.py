from typing import Optional
from app.config import Config
from app.pipeline.legacy import terraform_gen
from app.pipeline.legacy import git_ops
from app.pipeline.legacy import jenkins_ops
from app.pipeline import agno_agent


def run_pipeline(
    config: Config,
    prompt: str,
    commit_message: Optional[str] = None,
    jenkins_parameters: Optional[dict[str, str]] = None,
    skip_git: bool = False,
    skip_jenkins: bool = False,
    use_agno: bool = False,
    job_id: Optional[str] = None,
) -> dict:
    if use_agno:
        return _run_agno(config, prompt, commit_message, jenkins_parameters, skip_git, skip_jenkins, job_id)

    return _run_legacy(config, prompt, commit_message, jenkins_parameters, skip_git, skip_jenkins)


def _run_agno(
    config: Config,
    prompt: str,
    commit_message: Optional[str] = None,
    jenkins_parameters: Optional[dict[str, str]] = None,
    skip_git: bool = False,
    skip_jenkins: bool = False,
    job_id: Optional[str] = None,
) -> dict:
    return agno_agent.run(config, prompt, job_id=job_id, skip_git=skip_git, skip_jenkins=skip_jenkins)


def _run_legacy(
    config: Config,
    prompt: str,
    commit_message: Optional[str] = None,
    jenkins_parameters: Optional[dict[str, str]] = None,
    skip_git: bool = False,
    skip_jenkins: bool = False,
) -> dict:
    result = {}

    tf_result = terraform_gen.run(config, prompt)
    result["terraform"] = tf_result

    if not skip_git:
        msg = commit_message or prompt
        git_result = git_ops.run(config, tf_result["terraform_dir"], msg)
        result["git"] = git_result
    else:
        result["git"] = {"status": "skipped"}

    if not skip_jenkins:
        jenkins_result = jenkins_ops.run(config, jenkins_parameters)
        result["jenkins"] = jenkins_result
    else:
        result["jenkins"] = {"status": "skipped"}

    return result
