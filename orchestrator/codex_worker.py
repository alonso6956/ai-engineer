from pathlib import Path

from orchestrator.local_worker import CandidateResult
from providers.codex import run_codex
from tools.filesystem import FileSystem
from tools.git import GitManager
from tools.tests import TestRunner


CODEX_WORKER_PROMPT = """
You are the final escalation worker in an autonomous
software engineering system.

Work directly on the provided repository and solve the
requested task.

Rules:

1. Inspect the existing project before modifying files.
2. Make only changes necessary for the requested task.
3. Preserve existing behavior unless the task requires
   changing it.
4. Do not modify tests, test infrastructure, Git metadata,
   CI configuration, or validation infrastructure unless
   the task explicitly requires it.
5. Do not bypass, disable, monkey patch, intercept, skip,
   or manipulate tests or validation.
6. Do not commit changes.
7. Do not run destructive Git commands.
8. Do not modify files outside the repository.
9. Run appropriate tests or validation before finishing.
10. Leave the completed implementation in the working tree.

When the implementation is complete, stop.
"""


class CodexWorker:

    def __init__(
        self,
        project_root: str,
        timeout: int = 3600,
    ):
        self.project_root = str(
            Path(project_root).resolve()
        )

        self.timeout = timeout

        self.git = GitManager(
            self.project_root
        )

        self.fs = FileSystem(
            self.project_root
        )

        self.tests = TestRunner(
            self.project_root
        )

    def run_task(
        self,
        task: str,
    ) -> CandidateResult:

        if not self.git.is_clean():
            return CandidateResult(
                success=False,
                message=(
                    "Repository must be clean "
                    "before Codex starts."
                ),
                diff="",
                git_status=self.git.status().stdout,
                tests_output="",
                changed_files=[],
            )

        initial_untracked = set(
            self.git.untracked_files()
        )

        prompt = f"""
{CODEX_WORKER_PROMPT}

TASK:

{task}
"""

        try:
            output = run_codex(
                prompt,
                cwd=self.project_root,
                sandbox="workspace-write",
            )

        except Exception as error:
            self._rollback(
                initial_untracked
            )

            return CandidateResult(
                success=False,
                message=(
                    "Codex worker failed: "
                    f"{type(error).__name__}: "
                    f"{error}"
                ),
                diff="",
                git_status="",
                tests_output="",
                changed_files=[],
            )

        changed_files = (
            self.git.changed_files()
        )

        #
        # Codex saying it completed the task is
        # insufficient. There must be actual changes.
        #
        if not changed_files:
            return CandidateResult(
                success=False,
                message=(
                    "Codex completed without "
                    "producing any file changes."
                ),
                diff="",
                git_status=self.git.status().stdout,
                tests_output="",
                changed_files=[],
            )

        #
        # Deterministic protection against modifying
        # validation infrastructure.
        #
        protected = self._protected_changes(
            changed_files
        )

        if protected:
            self._rollback(
                initial_untracked
            )

            return CandidateResult(
                success=False,
                message=(
                    "Codex modified protected "
                    "validation files: "
                    + ", ".join(protected)
                ),
                diff="",
                git_status="",
                tests_output="",
                changed_files=[],
            )

        tests = self.tests.run_pytest()

        if not tests.success:
            diff = self.git.diff()

            self._rollback(
                initial_untracked
            )

            return CandidateResult(
                success=False,
                message=(
                    "Codex implementation failed "
                    "final tests."
                ),
                diff=diff.stdout,
                git_status="",
                tests_output=str(tests),
                changed_files=[],
            )

        diff = self.git.diff()
        status = self.git.status()

        return CandidateResult(
            success=True,
            message=(
                "Codex produced a candidate "
                "and deterministic validation passed."
            ),
            diff=diff.stdout,
            git_status=status.stdout,
            tests_output=str(tests),
            changed_files=changed_files,
        )

    def _protected_changes(
        self,
        changed_files: list[str],
    ) -> list[str]:

        protected = []

        exact_names = {
            "conftest.py",
            "pytest.ini",
            "tox.ini",
        }

        protected_directories = {
            ".git",
            ".github",
        }

        for file_path in changed_files:

            path = Path(file_path)

            if path.name in exact_names:
                protected.append(
                    file_path
                )
                continue

            if (
                path.name.startswith("test_")
                and path.suffix == ".py"
            ):
                protected.append(
                    file_path
                )
                continue

            if (
                path.name.endswith("_test.py")
            ):
                protected.append(
                    file_path
                )
                continue

            if any(
                part in protected_directories
                for part in path.parts
            ):
                protected.append(
                    file_path
                )

        return protected

    def _rollback(
        self,
        initial_untracked: set[str],
    ) -> None:

        reset_result = (
            self.git.reset_index()
        )

        if not reset_result.success:
            raise RuntimeError(
                "Failed to reset Git index:\n"
                f"{reset_result}"
            )

        restore_result = (
            self.git.restore_worktree()
        )

        if not restore_result.success:
            raise RuntimeError(
                "Failed to restore working tree:\n"
                f"{restore_result}"
            )

        current_untracked = set(
            self.git.untracked_files()
        )

        new_untracked = (
            current_untracked
            - initial_untracked
        )

        for path in new_untracked:
            self.fs.delete_file(
                path
            )