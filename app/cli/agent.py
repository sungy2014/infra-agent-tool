#!/usr/bin/env python3
import sys
import json
import warnings
import click

warnings.filterwarnings("ignore", message=".*OpenSSL.*")
from app.config import Config
from app.pipeline.core import run_pipeline


@click.group()
def cli():
    pass


@cli.command()
@click.argument("prompt", nargs=-1, required=True)
@click.option("--commit-msg", default=None, help="Git commit message")
@click.option("--jenkins-params", default=None, help="JSON dict of Jenkins build parameters")
@click.option("--skip-git", is_flag=True, help="Skip git operations")
@click.option("--skip-jenkins", is_flag=True, help="Skip Jenkins trigger")
def generate(prompt, commit_msg, jenkins_params, skip_git, skip_jenkins):
    """Generate Terraform code, commit to GitHub, and trigger Jenkins."""
    config = Config()
    config.validate()

    user_prompt = " ".join(prompt)
    try:
        params = json.loads(jenkins_params) if jenkins_params else None
    except json.JSONDecodeError as e:
        click.echo(f"Invalid --jenkins-params JSON: {e}", err=True)
        sys.exit(1)

    try:
        result = run_pipeline(
            config=config,
            prompt=user_prompt,
            commit_message=commit_msg,
            jenkins_parameters=params,
            skip_git=skip_git,
            skip_jenkins=skip_jenkins,
        )
        click.echo(json.dumps(result, indent=2))
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command()
def validate():
    """Validate env configuration without generating code."""
    config = Config()
    config.validate()
    click.echo("Configuration loaded OK")
    click.echo(f"  LLM provider: {config.llm_provider}")
    click.echo(f"  LLM model   : {config.llm_model}")
    click.echo(f"  Git remote  : {config.git_remote_url or '(not set)'}")
    click.echo(f"  Jenkins URL : {config.jenkins_url or '(not set)'}")
    click.echo(f"  Jenkins job : {config.jenkins_job_name or '(not set)'}")


@cli.command()
def serve():
    """Start the REST API server."""
    from app.server import main
    main()


if __name__ == "__main__":
    cli()
