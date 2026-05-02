---
name: jenkins-pipeline
description: Trigger a Jenkins pipeline job to apply Terraform infrastructure. Use after pushing code to GitHub.
allowed-tools: Bash(curl *)
---

# Jenkins Pipeline

Trigger a Jenkins job to apply the generated Terraform infrastructure.

## Workflow

1. **Prepare parameters** — Collect the job name, Jenkins URL, username, and API token from the environment configuration
2. **Trigger the job** — Use `trigger_jenkins_job(job_name, jenkins_url, username, api_token, parameters)` to start the pipeline
3. **Confirm** — The tool returns the queue location URL if the trigger succeeds

## Build parameters

The Jenkins job accepts the following parameters by default:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `TERRAFORM_DIR` | `terraform` | Directory containing Terraform files |

Pass additional parameters as a JSON string when needed.

## Rules

- Verify Jenkins is accessible before triggering
- Use the API token for authentication (not the user password)
- Log the queue URL returned by the trigger for tracking
- If the trigger returns a non-201 status code, report the error with the response body

## Example

```
trigger_jenkins_job(
    job_name="terraform-apply-pipeline",
    jenkins_url="http://localhost:8080",
    username="admin",
    api_token="***",
    parameters='{"TERRAFORM_DIR": "terraform"}'
)
```
