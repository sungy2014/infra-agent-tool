---
name: git-operations
description: Clone, commit, and push Terraform code to a GitHub repository. Use after generating Terraform files.
allowed-tools: Bash(git *) Write *
---

# Git Operations

Manage the Git lifecycle for Terraform code: cloning the target repository, committing changes, and pushing to GitHub.

## Workflow

### 1. Clone or pull

Use `clone_or_pull_repo(remote_url, branch)` to get the latest code:

- If the repo directory doesn't exist, it clones the full repository
- If it already exists, it pulls the latest changes from the remote

### 2. Commit and push

Use `git_commit_and_push(commit_message, branch)` to publish changes:

- Stages all modified and new files via `git add -A`
- Creates a commit with the provided message
- Pushes to the remote origin on the specified branch
- Skips automatically if there are no changes to commit

## Commit message conventions

| Prefix | Use case |
|--------|----------|
| `feat:` | New infrastructure resource |
| `fix:` | Bug fix or configuration correction |
| `chore:` | Maintenance, refactoring, or cleanup |
| `docs:` | Documentation-only changes |

Include a brief description of what was created or changed. Example:
- `feat: add S3 bucket with versioning enabled`
- `fix: update security group ingress rules`
- `chore: refactor variable naming conventions`

## Rules

- Always pull latest before pushing to avoid conflicts
- Use descriptive commit messages that explain what changed and why
- Never commit sensitive data (access keys, passwords, tokens)
- If a push fails, check remote connectivity and credentials before retrying
