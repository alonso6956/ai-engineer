from orchestrator.events import activity
import subprocess
from dataclasses import dataclass
from pathlib import Path
from paths import normalize_path


@dataclass
class GitResult:
    success: bool
    return_code: int
    stdout: str
    stderr: str

    def __str__(self) -> str:
        status = "OK" if self.success else "ERROR"

        parts = [
            f"Status: {status}",
            f"Return code: {self.return_code}",
        ]

        if self.stdout.strip():
            parts.append(f"\nSTDOUT:\n{self.stdout.strip()}")

        if self.stderr.strip():
            parts.append(f"\nSTDERR:\n{self.stderr.strip()}")

        return "\n".join(parts)


class GitManager:
    """
    Operaciones Git controladas sobre un único proyecto.
    """

    def __init__(self, project_root: str):
        self.root = normalize_path(project_root)

        if not self.root.exists():
            raise FileNotFoundError(
                f"El proyecto no existe: {self.root}"
            )

        if not self.root.is_dir():
            raise NotADirectoryError(
                f"No es un directorio: {self.root}"
            )

        check = self._run(
            ["rev-parse", "--is-inside-work-tree"]
        )

        if not check.success:
            raise RuntimeError(
                f"No es un repositorio Git: {self.root}"
            )

    @activity('Git')
    def _run(self, args: list[str]) -> GitResult:
        return self._run_in(self.root, args)

    @staticmethod
    def _run_in(root: Path, args: list[str]) -> GitResult:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
        )

        return GitResult(
            success=result.returncode == 0,
            return_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    @classmethod
    def initialize(cls, project_root: str) -> "GitManager":
        """Initialize a repository in an existing directory, then validate it."""
        root = normalize_path(project_root)
        result = cls._run_in(root, ["init"])
        if not result.success:
            raise RuntimeError(f"Git initialization failed:\n{result}")
        return cls(str(root))

    def status(self) -> GitResult:
        return self._run([
            "status",
            "--short",
        ])

    def diff(self) -> GitResult:
        return self._run([
            "diff",
        ])

    def is_clean(self) -> bool:
        return not self.status().stdout.strip()

    def changed_files(self) -> list[str]:
        result = self._run([
            "diff",
            "--name-only",
        ])

        if not result.success:
            raise RuntimeError(
                f"No se pudieron obtener los archivos modificados:\n{result}"
            )

        return [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]

    def diff_staged(self) -> GitResult:
        return self._run([
            "diff",
            "--cached",
        ])

    def add_all(self) -> GitResult:
        return self._run([
            "add",
            "-A",
        ])

    def commit(self, message: str) -> GitResult:
        if not message.strip():
            raise ValueError(
                "El mensaje del commit no puede estar vacío."
            )

        return self._run([
            "commit",
            "-m",
            message,
        ])

    def current_branch(self) -> GitResult:
        return self._run([
            "branch",
            "--show-current",
        ])

    def head(self) -> GitResult:
        return self._run([
            "rev-parse",
            "HEAD",
        ])

    def reset_index(self) -> GitResult:
        """
        Restaura el índice de Git a HEAD sin tocar
        los archivos del working tree.
        """
        return self._run([
            "reset",
            "--mixed",
            "HEAD",
        ])

    def restore_worktree(self) -> GitResult:
        """
        Restaura archivos tracked modificados al estado de HEAD.

        No elimina archivos nuevos/untracked.
        """

        return self._run([
            "restore",
            ".",
        ])

    def clean_untracked(self) -> GitResult:
        """
        Elimina archivos y directorios untracked.

        Debe utilizarse deliberadamente: es destructivo.
        """

        return self._run([
            "clean",
            "-fd",
        ])

    def untracked_files(self) -> list[str]:
        """
        Devuelve los archivos y directorios untracked del repositorio.
        """

        result = self._run([
            "status",
            "--porcelain",
            "--untracked-files=all",
        ])

        if not result.success:
            raise RuntimeError(
                f"No se pudo obtener git status:\n{result}"
            )

        files = []

        for line in result.stdout.splitlines():
            if line.startswith("?? "):
                files.append(line[3:])

        return files
