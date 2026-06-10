"""``opensesame`` CLI — model management + a policy/capability dry-run.

    opensesame check [--policy opensesame.toml]
    opensesame download audio  --model openai/whisper-base.en
    opensesame download vision --model verytuffcat/recaptcha
    opensesame download ocr    --model anuashok/ocr-captcha-v3

``check`` loads + validates the policy and reports which engines and model
providers are available (so you see what an ML extra still needs to install).
``download`` fetches a model into the local cache via huggingface_hub.
"""

from __future__ import annotations

import sys

import rich_click as click

from open_sesame.api.defaults import default_solver, install_default_providers
from open_sesame.api.policy import SolverPolicy, load_policy
from open_sesame.api.registry import default_registry
from open_sesame.api.result import Family

# Sensible default model per use case (override with --model).
_KIND_DEFAULTS = {
    "audio": "openai/whisper-base.en",
    "vision": "verytuffcat/recaptcha",
    "ocr": "anuashok/ocr-captcha-v3",
}
_KIND_PROVIDER = {"audio": "whisper", "vision": "tiles", "ocr": "ocr"}


@click.group()
@click.version_option(package_name="open-sesame")
def cli() -> None:
    """OpenSesame — self-hosted captcha solving, no paid solver APIs."""


@cli.command()
@click.option("--policy", "policy_path", type=click.Path(exists=True), default=None,
              help="Path to an opensesame.toml policy. Defaults are used if omitted.")
def check(policy_path: str | None) -> None:
    """Load + validate policy and report engine/model availability (dry-run)."""

    try:
        policy = load_policy(policy_path) if policy_path else SolverPolicy()
    except Exception as exc:  # noqa: BLE001 - surface validation errors cleanly
        click.secho(f"✗ policy invalid: {exc}", fg="red")
        sys.exit(1)

    solver = default_solver(policy)
    click.secho("OpenSesame policy OK", fg="green", bold=True)
    click.echo(f"  policy_id        {policy.policy_id}")
    click.echo(f"  allow_sites      {list(policy.allow_sites) or '(none — default-deny)'}")
    click.echo(f"  device           {policy.device}")
    click.echo(f"  timeouts (s)     auto={policy.auto_timeout_s} manual={policy.manual_timeout_s} "
               f"queue={policy.queue_timeout_s}")
    click.echo(f"  escalate_on_fail {policy.escalate_on_fail}")
    click.echo(f"  audit_log        {policy.audit_log or '(disabled)'}")

    reg = solver.registry
    click.echo("\nEngines:")
    for family in (Family.RECAPTCHA_V2, Family.RECAPTCHA_V2_INVISIBLE, Family.OCR):
        present = family in solver._engines  # noqa: SLF001 - introspection for the dry-run
        click.echo(f"  {_mark(present)} {family.value}")

    click.echo("\nModel providers:")
    for kind, key in _KIND_PROVIDER.items():
        present = reg.has_factory(key)
        hint = "" if present else "  (install the matching ml extra + solver module)"
        click.echo(f"  {_mark(present)} {kind:6} [{key}]{hint}")

    if not policy.allow_sites:
        click.secho("\nNote: allow_sites is empty — this policy solves nothing until you add hosts.",
                    fg="yellow")


@cli.command()
@click.argument("kind", type=click.Choice(sorted(_KIND_DEFAULTS)))
@click.option("--model", "model_id", default=None, help="Model id (HF repo). Defaults per kind.")
@click.option("--cache-dir", default=".local/hf", show_default=True)
def download(kind: str, model_id: str | None, cache_dir: str) -> None:
    """Download a model for a use case (audio | vision | ocr) into the local cache."""

    model_id = model_id or _KIND_DEFAULTS[kind]
    try:
        from huggingface_hub import snapshot_download
    except Exception:
        click.secho("✗ huggingface_hub not installed. Install an ml extra:", fg="red")
        click.echo("    pip install 'open-sesame[ml-audio]'   # or ml-vision")
        sys.exit(1)

    click.echo(f"Downloading {kind} model {model_id} -> {cache_dir} ...")
    try:
        path = snapshot_download(model_id, cache_dir=cache_dir)
    except Exception as exc:  # noqa: BLE001
        click.secho(f"✗ download failed: {exc}", fg="red")
        sys.exit(1)
    click.secho(f"✓ {model_id} ready at {path}", fg="green")


def _mark(ok: bool) -> str:
    return click.style("✓", fg="green") if ok else click.style("·", fg="yellow")


def main() -> None:
    install_default_providers(default_registry())
    cli()


if __name__ == "__main__":
    main()
