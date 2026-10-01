import json
import config
import os
import subprocess

from config import CODEX_SANDBOX
from paths import normalize_path


def run_codex(
    prompt: str,
    cwd: str,
    sandbox: str = CODEX_SANDBOX,
) -> str:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            "codex",
            "exec",
            *model_arguments(),
            "--skip-git-repo-check",
            "--sandbox",
            sandbox,
            prompt,
        ],
        cwd=str(normalize_path(cwd)),
        env=environment,
        capture_output=True,
        text=True,
        timeout=3600,
    )

    if result.returncode != 0:
        raise RuntimeError(codex_failure("Codex failed", result.stderr))

    return result.stdout


def model_arguments():
    return ["--model", config.CODEX_MODEL] if config.CODEX_MODEL else []


def codex_failure(label, stderr):
    """Put the actionable cause before verbose CLI headers and echoed prompts."""
    reasons = []
    for line in stderr.splitlines():
        if line.startswith("ERROR:"):
            value = line.partition(":")[2].strip()
            try:
                payload = json.loads(value)
                error = payload.get("error", payload)
                reason = error.get("message", value) if isinstance(error, dict) else str(error)
            except (ValueError, AttributeError):
                reason = value
            if reason not in reasons:
                reasons.append(reason)
    if not reasons:
        reasons = [line.strip() for line in stderr.splitlines() if line.strip()][-1:]
    cause = "; ".join(reasons) or "No diagnostic output"
    hint = ""
    if "not supported" in cause and "model" in cause.lower():
        hint = " Selecciona un modelo disponible con /model codex <id> en la TUI de Raphael o CODEX_MODEL en .env."
    return f"{label}: {cause}{hint}\n\nSTDERR:\n{stderr}"
