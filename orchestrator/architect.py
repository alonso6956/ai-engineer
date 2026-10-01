from __future__ import annotations
from orchestrator.events import activity

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paths import normalize_path
from providers.codex import model_arguments, codex_failure
from orchestrator.task_manager import Task


class ArchitectError(RuntimeError):
    pass


@dataclass
class ArchitecturePlan:
    summary: str
    tasks: list[Task]


class CodexArchitect:
    """
    Uses Codex as a read-only software architect.

    Responsibilities:
    - inspect the existing repository
    - understand the requested goal
    - decompose the goal into implementation tasks
    - define dependencies
    - define acceptance criteria

    It MUST NOT modify the repository.
    """

    def __init__(
        self,
        project_root: str,
        timeout: int = 3600,
    ):
        self.project_root = normalize_path(project_root)

        self.timeout = timeout

        if not self.project_root.exists():
            raise ArchitectError(
                f"Project root does not exist: "
                f"{self.project_root}"
            )

        if not self.project_root.is_dir():
            raise ArchitectError(
                f"Project root is not a directory: "
                f"{self.project_root}"
            )

    @activity('Codex architect')
    def create_plan(
        self,
        goal: str,
    ) -> ArchitecturePlan:

        if not goal.strip():
            raise ArchitectError(
                "Architecture goal cannot be empty."
            )

        prompt = self._build_prompt(goal)

        result = subprocess.run(
            [
                "codex",
                "exec",
                *model_arguments(),
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                prompt,
            ],
            cwd=str(self.project_root),
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )

        if result.returncode != 0:
            raise ArchitectError(
                codex_failure("Codex architect failed", result.stderr)
            )

        return self._parse_response(
            result.stdout
        )

    def _build_prompt(
        self,
        goal: str,
    ) -> str:

        return f"""
You are the software architect for an autonomous coding system.

PROJECT ROOT:
{self.project_root}

HIGH-LEVEL GOAL:
{goal}

Your job is to inspect the existing repository and create a concrete
implementation plan.

You are operating in READ-ONLY mode.

You MUST NOT:
- modify files
- create files
- delete files
- run destructive commands
- make Git commits
- change Git state
- install dependencies
- execute the implementation

You MAY:
- inspect files
- inspect directories
- inspect Git history
- inspect Git status
- inspect existing tests
- inspect configuration
- reason about the architecture

PLANNING RULES:

1. Inspect the repository before producing the plan.

2. Respect the existing architecture and conventions.

3. Break the goal into small, independently implementable tasks.

4. Each task should ideally correspond to one atomic Git commit.

5. Tasks must be ordered through explicit dependencies.

6. Avoid giant tasks such as:
   "Implement the entire feature."

7. Avoid meaningless micro-tasks such as:
   "Open file."
   "Read code."
   "Think about implementation."

8. Inspection and reasoning are architect responsibilities, not separate
   implementation tasks.

9. Each task description must contain enough context for a coding worker
   that has not seen this planning conversation.

10. Each task must contain objective acceptance criteria.

11. Preserve existing behavior unless the goal explicitly requires a
    behavior change.

12. Do not create tasks whose only purpose is running tests.
    Validation is already handled by the execution system.

13. Do not create tasks whose only purpose is reviewing previous tasks.
    Review is handled separately.

14. Do not instruct workers to make Git commits.
    The execution system handles commits.

15. Dependencies must reference task IDs that exist in this plan.

16. A task must never depend on itself.

17. Do not create circular dependencies.

18. Use IDs exactly in this format:

    task-001
    task-002
    task-003

19. IDs must be sequential.

20. commit_message must be concise and suitable for Git.

21. If a task legitimately requires modifying existing or new test files,
    list the exact repository-relative paths in allowed_test_files.

22. allowed_test_files grants permission only to those exact test files.

23. Never include validation infrastructure such as:
    conftest.py
    pytest.ini
    tox.ini
    .git/*
    .github/*

24. If the task does not require test changes, allowed_test_files must be [].

OUTPUT:

Return ONLY valid JSON.

Do not use Markdown.
Do not use code fences.
Do not include commentary before or after the JSON.

Use exactly this structure:

{{
  "summary": "Short architectural summary.",
  "tasks": [
    {{
      "id": "task-001",
      "description": "Concrete implementation instructions.",
      "commit_message": "Concise commit message",
      "depends_on": [],
      "acceptance_criteria": [
        "Objective criterion 1"
      ],
      "allowed_test_files": [
        "test_calculator.py"
      ]
    }},
    {{
      "id": "task-002",
      "description": "Concrete implementation instructions.",
      "commit_message": "Concise commit message",
      "depends_on": ["task-001"],
      "acceptance_criteria": [
        "Objective criterion 1"
      ],
      "allowed_test_files": []
    }}
  ]
}}
""".strip()

    def _parse_response(
        self,
        output: str,
    ) -> ArchitecturePlan:

        raw = self._extract_json(output)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ArchitectError(
                "Codex returned invalid JSON."
            ) from exc

        self._validate_top_level(data)

        tasks: list[Task] = []

        for index, raw_task in enumerate(
            data["tasks"],
            start=1,
        ):
            task = self._parse_task(
                raw_task,
                index,
            )

            tasks.append(task)

        if not tasks:
            raise ArchitectError(
                "Codex returned an empty task plan."
            )

        self._validate_dependencies(tasks)
        self._validate_test_permissions(tasks)

        return ArchitecturePlan(
            summary=data["summary"].strip(),
            tasks=tasks,
        )

    def _extract_json(
        self,
        output: str,
    ) -> str:
        """
        Codex CLI may occasionally include diagnostic text around
        the final response. Extract the outermost JSON object.
        """

        output = output.strip()

        if not output:
            raise ArchitectError(
                "Codex returned an empty response."
            )

        start = output.find("{")
        end = output.rfind("}")

        if start == -1 or end == -1:
            raise ArchitectError(
                "No JSON object found in Codex response."
            )

        if end <= start:
            raise ArchitectError(
                "Malformed JSON response from Codex."
            )

        return output[start : end + 1]

    def _validate_top_level(
        self,
        data: Any,
    ) -> None:

        if not isinstance(data, dict):
            raise ArchitectError(
                "Architecture response must be a JSON object."
            )

        summary = data.get("summary")

        if not isinstance(summary, str):
            raise ArchitectError(
                "Architecture plan requires a summary."
            )

        if not summary.strip():
            raise ArchitectError(
                "Architecture summary cannot be empty."
            )

        tasks = data.get("tasks")

        if not isinstance(tasks, list):
            raise ArchitectError(
                "Architecture plan requires a tasks list."
            )

    def _parse_task(
        self,
        data: Any,
        index: int,
    ) -> Task:

        if not isinstance(data, dict):
            raise ArchitectError(
                f"Task {index} must be an object."
            )

        expected_id = (
            f"task-{index:03d}"
        )

        task_id = data.get("id")

        if task_id != expected_id:
            raise ArchitectError(
                f"Expected task ID {expected_id}, "
                f"got {task_id!r}."
            )

        description = data.get(
            "description"
        )

        commit_message = data.get(
            "commit_message"
        )

        depends_on = data.get(
            "depends_on",
            [],
        )

        acceptance_criteria = data.get(
            "acceptance_criteria",
            [],
        )

        allowed_test_files = data.get(
            "allowed_test_files",
            [],
        )

        if (
            not isinstance(description, str)
            or not description.strip()
        ):
            raise ArchitectError(
                f"{task_id} requires a description."
            )

        if (
            not isinstance(commit_message, str)
            or not commit_message.strip()
        ):
            raise ArchitectError(
                f"{task_id} requires a commit_message."
            )

        if not isinstance(
            depends_on,
            list,
        ):
            raise ArchitectError(
                f"{task_id}.depends_on must be a list."
            )

        if not all(
            isinstance(item, str)
            and item.strip()
            for item in depends_on
        ):
            raise ArchitectError(
                f"{task_id}.depends_on contains "
                f"invalid values."
            )

        if not isinstance(
            acceptance_criteria,
            list,
        ):
            raise ArchitectError(
                f"{task_id}.acceptance_criteria "
                f"must be a list."
            )

        if not acceptance_criteria:
            raise ArchitectError(
                f"{task_id} requires at least one "
                f"acceptance criterion."
            )

        if not all(
            isinstance(item, str)
            and item.strip()
            for item in acceptance_criteria
        ):
            raise ArchitectError(
                f"{task_id}.acceptance_criteria "
                f"contains invalid values."
            )

        if not isinstance(
            allowed_test_files,
            list,
        ):
            raise ArchitectError(
                f"{task_id}.allowed_test_files "
                f"must be a list."
            )

        if not all(
            isinstance(item, str)
            and item.strip()
            for item in allowed_test_files
        ):
            raise ArchitectError(
                f"{task_id}.allowed_test_files "
                f"contains invalid values."
            )

        return Task(
            id=task_id,
            description=description.strip(),
            commit_message=commit_message.strip(),
            depends_on=[
                item.strip()
                for item in depends_on
            ],
            acceptance_criteria=[
                item.strip()
                for item
                in acceptance_criteria
            ],
            allowed_test_files=[
                item.strip()
                for item in allowed_test_files
            ],
        )

    def _validate_dependencies(
        self,
        tasks: list[Task],
    ) -> None:

        task_ids = {
            task.id
            for task in tasks
        }

        for task in tasks:

            seen_dependencies: set[str] = set()

            for dependency in task.depends_on:

                if dependency not in task_ids:
                    raise ArchitectError(
                        f"{task.id} depends on unknown "
                        f"task {dependency}."
                    )

                if dependency == task.id:
                    raise ArchitectError(
                        f"{task.id} cannot depend on itself."
                    )

                if dependency in seen_dependencies:
                    raise ArchitectError(
                        f"{task.id} contains duplicate "
                        f"dependency {dependency}."
                    )

                seen_dependencies.add(
                    dependency
                )

        self._validate_no_cycles(tasks)

    def _validate_test_permissions(
        self,
        tasks: list[Task],
    ) -> None:

        forbidden_names = {
            "conftest.py",
            "pytest.ini",
            "tox.ini",
        }

        forbidden_prefixes = (
            ".git/",
            ".github/",
        )

        for task in tasks:
            seen: set[str] = set()

            for raw_path in task.allowed_test_files:
                path = Path(raw_path)
                normalized = path.as_posix()

                if path.is_absolute():
                    raise ArchitectError(
                        f"{task.id} contains absolute test "
                        f"path: {normalized}"
                    )

                if ".." in path.parts:
                    raise ArchitectError(
                        f"{task.id} contains unsafe test "
                        f"path: {normalized}"
                    )

                if normalized in forbidden_names:
                    raise ArchitectError(
                        f"{task.id} cannot authorize protected "
                        f"file {normalized}."
                    )

                if any(
                    normalized.startswith(prefix)
                    for prefix in forbidden_prefixes
                ):
                    raise ArchitectError(
                        f"{task.id} cannot authorize protected "
                        f"path {normalized}."
                    )

                if normalized in seen:
                    raise ArchitectError(
                        f"{task.id} contains duplicate test "
                        f"permission {normalized}."
                    )

                seen.add(normalized)

    def _validate_no_cycles(
        self,
        tasks: list[Task],
    ) -> None:

        dependencies = {
            task.id: task.depends_on
            for task in tasks
        }

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(
            task_id: str,
        ) -> None:

            if task_id in visited:
                return

            if task_id in visiting:
                raise ArchitectError(
                    "Circular dependency detected "
                    f"involving {task_id}."
                )

            visiting.add(task_id)

            for dependency in dependencies[
                task_id
            ]:
                visit(dependency)

            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in dependencies:
            visit(task_id)
