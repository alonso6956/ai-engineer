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
        raise RuntimeError(result.stderr)

    return result.stdout
