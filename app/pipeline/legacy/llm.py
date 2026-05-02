from openai import OpenAI

from app.config import Config

TERRAFORM_SYSTEM_PROMPT = """You are a senior infrastructure engineer specializing in Terraform.

Generate valid, production-quality Terraform code based on the user's request.

Rules:
- Output each file with a header: ### filename.tf
- Followed by a fenced code block containing the Terraform HCL code.
- Use Terraform >= 1.5 syntax.
- Use `terraform { required_providers { ... } }` blocks.
- Use variables where appropriate.
- Include output values for key resources.
- Default to AWS provider unless specified otherwise.
- Never include placeholder values like "CHANGEME" — use real defaults or prompt the user.
- Keep related resources in the same file for simplicity (e.g. VPC + subnets + route tables in network.tf).
"""


def _build_client(config: Config) -> OpenAI:
    kwargs = {"api_key": config.llm_api_key}
    if config.llm_base_url:
        kwargs["base_url"] = config.llm_base_url
    elif config.llm_provider == "deepseek":
        kwargs["base_url"] = "https://api.deepseek.com"
    return OpenAI(**kwargs)


def generate_terraform(config: Config, prompt: str) -> str:
    client = _build_client(config)

    kwargs = {
        "model": config.llm_model,
        "messages": [
            {"role": "system", "content": TERRAFORM_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }
    if config.llm_reasoning_effort:
        kwargs["reasoning_effort"] = config.llm_reasoning_effort

    resp = client.chat.completions.create(**kwargs)

    return resp.choices[0].message.content
