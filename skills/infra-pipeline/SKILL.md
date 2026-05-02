---
name: infra-pipeline
description: Full end-to-end infrastructure workflow — generate Terraform code, commit to GitHub, and trigger Jenkins. Use when the user wants infrastructure created from scratch.
allowed-tools: Bash(*) Read Write Glob Grep
---

# Infrastructure Pipeline

Orchestrate the complete infrastructure lifecycle: generate Terraform code, push to GitHub, and trigger a Jenkins apply.

## Workflow

### Phase 1: Clone repository

Call `clone_or_pull_repo(remote_url, branch)` to set up the working directory.

- Remote URL and branch come from the environment configuration
- Use the `main` branch by default

### Phase 2: Generate Terraform code

1. Ask the user for any missing details using the `ask_user` tool (bucket name, region, instance type, etc.)
2. Write provider config, variables, resources, and outputs using `write_terraform_file`
3. Follow the generate-terraform skill rules for Terraform code quality

### Phase 3: Commit and push

1. Call `git_commit_and_push` with a descriptive commit message
2. Use the `feat:` prefix for new infrastructure
3. Follow the git-operations skill rules for commit conventions

### Phase 4: Trigger Jenkins

1. Call `trigger_jenkins_job` with the configured job name, URL, and credentials
2. Confirm the trigger succeeded
3. Follow the jenkins-pipeline skill rules for authentication and error handling

## Decision matrix

| User provides | Action |
|---------------|--------|
| Full details (name, region, config) | Generate code directly, no questions needed |
| Partial details (just "S3 bucket") | Use `ask_user` for missing details before generating |
| Only "create infrastructure" | Request high-level scope, then break down |
| Existing code to modify | Clone repo, read existing files, then modify |

## Rules

- Execute all 4 phases in order — do not skip any
- Each phase depends on the previous one completing successfully
- If any phase fails, report the error clearly and stop
- Use sensible defaults when the user doesn't specify (region: `us-east-1`)
- Do not proceed without required information — ask first
- Confirm each phase result before moving to the next

## Example session

User: "create an S3 bucket with versioning"

1. `clone_or_pull_repo("https://github.com/org/repo.git", "main")` → cloned
2. `ask_user("What should the bucket name be?")` → user: "my-bucket"
3. `write_terraform_file("provider.tf", ...)` → done
4. `write_terraform_file("s3.tf", ...)` → done
5. `write_terraform_file("variables.tf", ...)` → done
6. `write_terraform_file("outputs.tf", ...)` → done
7. `git_commit_and_push("feat: add S3 bucket with versioning")` → pushed
8. `trigger_jenkins_job(...)` → triggered, queue URL returned
9. Report summary to user
