"""Locations owned by ai-engineer and normalization of caller-supplied paths."""

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR / "workspace"


def normalize_path(path: str | Path) -> Path:
    """Expand the user's home and anchor relative input to the caller's CWD."""
    return Path(path).expanduser().resolve()
