---
name: generate-terraform
description: Generate Terraform code for AWS infrastructure based on natural language prompts. Use when the user describes infrastructure they want to create.
allowed-tools: Bash(echo *) Bash(cat *) Write *
context: fork
agent: general
---

# Generate Terraform Code

Generate valid, production-quality Terraform code based on the user's infrastructure request.

## Workflow

1. **Parse the request** — Identify the AWS resources needed (S3, VPC, EC2, RDS, etc.)
2. **Clarify if needed** — If the request is ambiguous, ask the user for specifics using the `ask_user` tool (bucket name, region, instance type, etc.)
3. **Generate files** — Write each Terraform file using `write_terraform_file`:

   | File | Purpose |
   |------|---------|
   | `provider.tf` | Provider config with `required_providers` block |
   | `variables.tf` | Input variables with sensible defaults |
   | `main.tf` or `<resource>.tf` | Core resource definitions |
   | `outputs.tf` | Output values for key resources |

## Rules

- Use Terraform >= 1.5 syntax
- Always include a `terraform { required_providers { ... } }` block
- Default to AWS provider `~> 5.0` unless specified otherwise
- Use variables for anything that could change (region, names, tags)
- Include output values for resource IDs, ARNs, and endpoints
- Never use placeholder values like "CHANGEME" — use real defaults
- Apply sensible defaults: region `us-east-1`, environment `production`
- Add security best practices: encryption, public access blocks, least-privilege IAM
- Tag all resources with `Name`, `Environment`, and `ManagedBy`

## Example

User: "create an S3 bucket with versioning"

1. Ask user for bucket name if not provided
2. Generate:
   - `provider.tf` — AWS provider, region variable
   - `variables.tf` — `bucket_name`, `environment`
   - `s3.tf` — `aws_s3_bucket`, `aws_s3_bucket_versioning`, `aws_s3_bucket_public_access_block`
   - `outputs.tf` — bucket ID, ARN, domain name
3. Write each file and confirm completion
