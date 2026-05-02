import os
import re
import pathlib
from app.config import Config
from app.pipeline.legacy.llm import generate_terraform


TERRAFORM_DIR = "terraform"


class TerraformGenError(Exception):
    pass


def _parse_files(content: str) -> list[tuple[str, str]]:
    pattern = r"###\s*([^\n]+)\s*\n```(?:hcl)?\n(.*?)```"
    matches = re.findall(pattern, content, re.DOTALL)
    if matches:
        return [(name.strip(), body.strip()) for name, body in matches]

    code_blocks = re.findall(r"```(?:hcl)?\n(.*?)```", content, re.DOTALL)
    if code_blocks:
        return [("main.tf", code_blocks[0].strip())]

    return []


def _ensure_dir(path: str):
    pathlib.Path(path).mkdir(parents=True, exist_ok=True)


def run(config: Config, user_prompt: str) -> dict:
    raw = generate_terraform(config, user_prompt)

    files = _parse_files(raw)

    if not files:
        raise TerraformGenError(
            "Could not parse Terraform files from LLM response."
        )

    _ensure_dir(TERRAFORM_DIR)

    written = []
    for filename, body in files:
        sanitized = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
        if not sanitized.endswith(".tf"):
            sanitized += ".tf"
        filepath = os.path.join(TERRAFORM_DIR, sanitized)
        with open(filepath, "w") as f:
            f.write(body)
        written.append(filepath)

    return {
        "terraform_dir": TERRAFORM_DIR,
        "files": written,
    }
