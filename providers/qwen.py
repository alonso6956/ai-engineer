"""Legacy entry point; Qwen now names an alias for the configured local model."""

from providers.local import run_local


def run_qwen(prompt: str) -> str:
    return run_local(prompt)
