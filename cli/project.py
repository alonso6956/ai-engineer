"""Create the foundation of a new project without invoking coding agents."""

import os
from pathlib import Path
from typing import Callable

from paths import normalize_path
from tools.git import GitManager


GITIGNORE = ".DS_Store\nThumbs.db\n*.swp\n*~\n"


class ProjectCreationError(RuntimeError):
    """Creation stopped at a known step; the new directory is kept for inspection."""


def create_project(
    name: str,
    parent_path: str | Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> Path:
    if (
        not name.strip()
        or name in {".", ".."}
        or Path(name).is_absolute()
        or "/" in name
        or "\\" in name
        or any(ord(character) < 32 for character in name)
    ):
        raise ValueError("Invalid project name: use a single directory name without / or \\.")

    parent = normalize_path(parent_path if parent_path is not None else Path.cwd())
    if not parent.exists():
        raise FileNotFoundError(f"Parent directory does not exist: {parent}")
    if not parent.is_dir():
        raise NotADirectoryError(f"Parent path is not a directory: {parent}")
    destination = parent / name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"{destination} already exists.")
    if destination.resolve().parent != parent:
        raise ValueError("Project name escapes the parent directory.")
    # Git directory/index overrides could redirect writes to an unrelated repo.
    overrides = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")
    if any(os.environ.get(key) for key in overrides):
        raise ValueError("Unset Git directory/index environment overrides before creating a project.")

    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    step = "Create project directory"
    created = False
    try:
        destination.mkdir()  # Exclusive creation: never reuse an existing directory.
        created = True
        report(f"Created {destination}")

        step = "Initialize Git repository"
        git = GitManager.initialize(str(destination))
        report("Initialized Git repository")

        step = "Create README.md"
        with (destination / "README.md").open("x", encoding="utf-8") as file:
            file.write(f"# {name}\n")
        report("Created README.md")

        step = "Create .gitignore"
        with (destination / ".gitignore").open("x", encoding="utf-8") as file:
            file.write(GITIGNORE)
        report("Created .gitignore")

        step = "Stage initial files"
        result = git.add_all()
        if not result.success:
            raise RuntimeError(str(result))

        step = "Create initial commit"
        result = git.commit("Initial commit")
        if not result.success:
            detail = str(result)
            if any(text in detail.lower() for text in ("identity", "user.name", "user.email", "ident name", "auto-detect email")):
                detail += "\nConfigure Git user.name and user.email before creating a project."
            raise RuntimeError(detail)
        report("Created initial commit")

        step = "Validate new repository"
        validated = GitManager(str(destination))
        head = validated.head()
        if not head.success or not head.stdout.strip():
            raise RuntimeError(f"Initial commit could not be verified:\n{head}")
    except (Exception, KeyboardInterrupt) as error:
        suffix = f"\nDirectory left for inspection: {destination}" if created else ""
        detail = str(error) or type(error).__name__
        raise ProjectCreationError(f"{step} failed: {detail}{suffix}") from error

    return destination
