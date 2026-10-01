from orchestrator.events import activity
from pathlib import Path

from paths import normalize_path
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
11. Run Python tests with PYTHONDONTWRITEBYTECODE=1 python -B -m pytest.
    Do not create or modify .pyc files or __pycache__ artifacts.

When the implementation is complete, stop.
"""


class CodexWorker:

    def __init__(
        self,
        project_root: str,
        timeout: int = 3600,
        allowed_test_files: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
    ):
        self.project_root = str(
            normalize_path(project_root)
        )

        self.timeout = timeout
        self.acceptance_criteria = list(
            acceptance_criteria or []
        )
        self.allowed_test_files = {
            Path(path).as_posix()
            for path in (allowed_test_files or [])
        }

        self.git = GitManager(
            self.project_root
        )

        self.fs = FileSystem(
            self.project_root,
            allowed_test_files=list(
                self.allowed_test_files
            ),
        )

        self.tests = TestRunner(
            self.project_root
        )

    def _test_permission_prompt(self) -> str:
        if not self.allowed_test_files:
            return """
TEST POLICY:
- Do not modify, create, rename, or delete test files.
- Do not modify test infrastructure.
"""

        allowed = "\n".join(
            f"- {path}"
            for path in sorted(self.allowed_test_files)
        )

        return f"""
TEST POLICY:
You may modify ONLY these test files:

{allowed}

No other test files may be modified, created,
renamed, or deleted.

Never modify test infrastructure including:
- conftest.py
- pytest.ini
- tox.ini
- .git/
- .github/

Do not skip, xfail, disable, monkeypatch, intercept,
or otherwise bypass validation.
"""

    @activity('Codex worker')
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

        criteria_text = "\n".join(
            f"- {criterion}"
            for criterion in self.acceptance_criteria
        )
        task_prompt = f"""
TASK:
{task}

ACCEPTANCE CRITERIA:
{criteria_text or "- None specified."}

All acceptance criteria are requirements of the task.
Do not declare the task complete unless they are satisfied.
""".strip()

        prompt = f"""
{CODEX_WORKER_PROMPT}

{self._test_permission_prompt()}

{task_prompt}
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

    def _is_test_file(
        self,
        path: Path,
    ) -> bool:
        return (
            path.name.startswith("test_")
            or path.name.endswith("_test.py")
            or "tests" in path.parts
        )

    def _is_always_protected(
        self,
        path: Path,
    ) -> bool:
        normalized = path.as_posix()

        return (
            normalized in {
                "conftest.py",
                "pytest.ini",
                "tox.ini",
            }
            or normalized.startswith(".git/")
            or normalized.startswith(".github/")
        )

    def _protected_changes(
        self,
        changed_files: list[str],
    ) -> list[str]:

        protected = []

        for file_path in changed_files:
            path = Path(file_path)
            normalized = path.as_posix()

            if self._is_always_protected(path):
                protected.append(file_path)
                continue

            if (
                self._is_test_file(path)
                and normalized not in self.allowed_test_files
            ):
                protected.append(file_path)

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
