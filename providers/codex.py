import subprocess

from config import CODEX_SANDBOX


def run_codex(
    prompt: str,
    cwd: str,
    sandbox: str = CODEX_SANDBOX,
) -> str:
    result = subprocess.run(
        [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            sandbox,
            prompt,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=3600,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr)

    return result.stdout