from typing import Optional
import requests
from requests.auth import HTTPBasicAuth
from app.config import Config


class JenkinsError(Exception):
    pass


def run(config: Config, parameters: Optional[dict[str, str]] = None) -> dict:
    if not config.jenkins_url:
        return {"status": "skipped", "reason": "JENKINS_URL not set"}

    job_url = f"{config.jenkins_url.rstrip('/')}/job/{config.jenkins_job_name}/buildWithParameters"
    auth = HTTPBasicAuth(config.jenkins_user, config.jenkins_api_token)

    if parameters:
        resp = requests.post(job_url, auth=auth, data=parameters, timeout=30)
    else:
        resp = requests.post(job_url, auth=auth, timeout=30)

    if resp.status_code == 201:
        location = resp.headers.get("Location", "unknown")
        return {"status": "triggered", "queue_location": location}
    else:
        raise JenkinsError(
            f"Jenkins trigger returned HTTP {resp.status_code}: {resp.text}"
        )
