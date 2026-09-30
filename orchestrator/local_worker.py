import json
from dataclasses import dataclass
from typing import Callable

from providers.local import run_local
from tools.filesystem import FileSystem
from tools.tests import TestRunner
from tools.git import GitManager


@dataclass
class CandidateResult:
    success: bool
    message: str
    diff: str
    git_status: str
    tests_output: str
    changed_files: list[str]


SYSTEM_PROMPT = """
You are an autonomous software engineering worker.

You are working inside a restricted project.

Available actions:

1. Read a file:
{"action": "read_file", "path": "filename.py"}

2. List project files:
{"action": "list_files"}

3. Show project tree:
{"action": "tree"}

4. Replace/write a file:
{
    "action": "write_file",
    "path": "filename.py",
    "content": "complete file content"
}

5. Run pytest:
{"action": "run_tests"}

6. Inspect git diff:
{"action": "git_diff"}

7. Inspect git status:
{"action": "git_status"}

8. Finish:
{
    "action": "finish",
    "message": "description of completed work"
}

RULES:

- Respond with exactly ONE JSON object.
- Do not use Markdown.
- Do not use code fences.
- Do not include explanations outside JSON.
- Never modify tests unless the task explicitly permits it.
- Read relevant files before modifying them.
- Run tests after modifying code.
- Only use "finish" after tests pass.
"""


class LocalWorker:

    def __init__(
        self,
        project_root: str,
        max_steps: int = 20,
        model_runner: Callable[[str], str] = run_local,
        allowed_test_files: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
    ):
        self.acceptance_criteria = list(
            acceptance_criteria or []
        )
        self.allowed_test_files = list(
            allowed_test_files or []
        )
        self.fs = FileSystem(
            project_root,
            allowed_test_files=self.allowed_test_files,
        )
        self.tests = TestRunner(project_root)
        self.git = GitManager(project_root)

        self.max_steps = max_steps
        self.model_runner = model_runner
        self.model_name = getattr(
            model_runner,
            "__name__",
            "model_runner",
        )

    def _rollback(
        self,
        initial_untracked: set[str],
    ) -> None:
        """
        Restaura archivos tracked y elimina únicamente
        archivos untracked creados durante esta tarea.
        """

        self.git.restore_worktree()

        current_untracked = set(
            self.git.untracked_files()
        )

        created_files = (
            current_untracked - initial_untracked
        )

        for path in sorted(created_files):
            try:
                self.fs.delete_file(path)
                print(
                    f"Rollback removed: {path}"
                )
            except Exception as error:
                print(
                    f"Rollback could not remove "
                    f"{path}: {error}"
                )

    def _execute_action(self, command: dict) -> str:

        action = command.get("action")

        if action == "read_file":
            return self.fs.read_file(command["path"])

        if action == "list_files":
            return "\n".join(
                self.fs.list_files()
            )

        if action == "tree":
            return "\n".join(
                self.fs.tree()
            )

        if action == "write_file":
            self.fs.write_file(
                command["path"],
                command["content"],
            )

            return (
                f"File written: "
                f"{command['path']}"
            )

        if action == "run_tests":
            return str(
                self.tests.run_pytest()
            )

        if action == "git_diff":
            return str(
                self.git.diff()
            )

        if action == "git_status":
            return str(
                self.git.status()
            )

        if action == "finish":
            return command.get(
                "message",
                "Task completed.",
            )

        raise ValueError(
            f"Unknown action: {action}"
        )

    def run(
        self,
        task: str,
    ) -> CandidateResult:

        # -------------------------
        # PRE-FLIGHT
        # -------------------------

        initial_status = self.git.status()

        if initial_status.stdout.strip():
            raise RuntimeError(
                "El proyecto contiene cambios antes "
                "de comenzar la tarea.\n\n"
                f"{initial_status}"
            )

        initial_untracked = set(
            self.git.untracked_files()
        )

        initial_head = self.git.head()

        if not initial_head.success:
            raise RuntimeError(
                "No se pudo obtener HEAD."
            )

        starting_commit = (
            initial_head.stdout.strip()
        )

        print(
            f"Starting from commit: "
            f"{starting_commit}"
        )

        # -------------------------
        # CONVERSATION
        # -------------------------

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
        conversation = f"""
    {SYSTEM_PROMPT}

    {task_prompt}
    """

        success = False

        try:

            for step in range(
                1,
                self.max_steps + 1,
            ):

                print(
                    f"\n[{self.model_name.upper()} STEP "
                    f"{step}/{self.max_steps}]"
                )

                response = self.model_runner(
                    conversation
                )

                print(
                    f"{self.model_name}: {response}"
                )

                try:
                    command = json.loads(
                        response
                    )

                except json.JSONDecodeError:

                    conversation += f"""

Your previous response was invalid JSON:

{response}

Respond again using exactly ONE valid JSON object.
"""
                    continue

                action = command.get(
                    "action"
                )

                try:
                    result = (
                        self._execute_action(
                            command
                        )
                    )

                except Exception as error:
                    result = (
                        f"ERROR: "
                        f"{type(error).__name__}: "
                        f"{error}"
                    )

                print(
                    f"Tool: {result}"
                )

                # -------------------------
                # FINISH REQUEST
                # -------------------------

                if action == "finish":

                    tests = (
                        self.tests.run_pytest()
                    )

                    if tests.success:
                        success = True

                        print(
                            "\nTASK COMPLETED"
                        )

                        print(tests)

                        break

                    result = (
                        "You attempted to finish, "
                        "but tests are still failing:\n"
                        f"{tests}"
                    )

                conversation += f"""

Your previous action:

{json.dumps(command)}

Tool result:

{result}

Choose the next action.
"""

        except Exception:
            print(
                "\nWORKER ERROR — "
                "rolling back."
            )

            self._rollback(initial_untracked)

            raise

        # -------------------------
        # FAILURE
        # -------------------------

        if not success:
            print(
                "\nTASK FAILED — "
                "rolling back changes."
            )

            self._rollback(initial_untracked)

            return CandidateResult(
                success=False,
                message="Worker could not produce a passing candidate.",
                diff="",
                git_status="",
                tests_output="",
                changed_files=[],
            )

        final_tests = self.tests.run_pytest()

        if not final_tests.success:
            print(
                "\nFINAL VALIDATION FAILED — "
                "rolling back changes."
            )

            self._rollback(initial_untracked)

            return CandidateResult(
                success=False,
                message="Final tests failed.",
                diff="",
                git_status="",
                tests_output=str(final_tests),
                changed_files=[],
            )

        final_diff = self.git.diff()
        final_status = self.git.status()
        changed_files = self.git.changed_files()

        print(
            "\nFINAL DIFF:"
        )
        print(final_diff.stdout)

        return CandidateResult(
            success=True,
            message="Candidate ready for external review.",
            diff=final_diff.stdout,
            git_status=final_status.stdout,
            tests_output=str(final_tests),
            changed_files=changed_files,
        )
