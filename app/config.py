import os
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

dotenv_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path)


@dataclass
class Config:
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "openai"))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4o"))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", ""))
    llm_reasoning_effort: str = field(default_factory=lambda: os.getenv("LLM_REASONING_EFFORT", ""))

    git_remote_url: str = field(default_factory=lambda: os.getenv("GIT_REMOTE_URL", ""))
    git_branch: str = field(default_factory=lambda: os.getenv("GIT_BRANCH", "main"))

    jenkins_url: str = field(default_factory=lambda: os.getenv("JENKINS_URL", ""))
    jenkins_user: str = field(default_factory=lambda: os.getenv("JENKINS_USER", ""))
    jenkins_api_token: str = field(default_factory=lambda: os.getenv("JENKINS_API_TOKEN", ""))
    jenkins_job_name: str = field(default_factory=lambda: os.getenv("JENKINS_JOB_NAME", ""))

    def validate(self):
        missing = []
        if not self.llm_api_key:
            missing.append("LLM_API_KEY")
        if missing:
            raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
