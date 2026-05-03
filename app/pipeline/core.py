from typing import Optional
from app.config import Config
from app.pipeline import agno_agent


def run_pipeline(
    config: Config,
    prompt: str,
    commit_message: Optional[str] = None,
    jenkins_parameters: Optional[dict[str, str]] = None,
    skip_git: bool = False,
    skip_jenkins: bool = False,
    job_id: Optional[str] = None,
) -> dict:
    return agno_agent.run(
        config, prompt, job_id=job_id,
        commit_message=commit_message,
        jenkins_parameters=jenkins_parameters,
        skip_git=skip_git, skip_jenkins=skip_jenkins,
    )
