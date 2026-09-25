import subprocess

from config import CODEX_SANDBOX


def run_codex(prompt: str, cwd: str) -> str:
    result = subprocess.run(
        [
            "codex",
            "exec",
            "--sandbox",
            CODEX_SANDBOX,
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