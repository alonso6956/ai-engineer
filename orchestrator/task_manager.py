from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from paths import WORKSPACE_DIR, normalize_path

VALID_TASK_STATUSES = {
    "pending",
    "running",
    "completed",
    "failed",
}

VALID_PLAN_STATUSES = {
    "pending",
    "running",
    "completed",
    "failed",
}


@dataclass
class Task:
    id: str
    description: str
    commit_message: str
    depends_on: list[str] = field(
        default_factory=list
    )
    acceptance_criteria: list[str] = field(
        default_factory=list
    )
    allowed_test_files: list[str] = field(
        default_factory=list
    )
    status: str = "pending"
    commit_hash: str | None = None
    attempts: int = 0

    def __post_init__(self) -> None:
        if self.status not in VALID_TASK_STATUSES:
            raise ValueError(
                f"Invalid task status: {self.status}"
            )
        if self.attempts < 0:
            raise ValueError("Task attempts cannot be negative.")


@dataclass
class ProjectPlan:
    version: int
    goal: str
    project_root: str
    status: str = "pending"
    current_task_id: str | None = None
    tasks: list[Task] = field(default_factory=list)
    budget_usage: dict[str, int] = field(
        default_factory=lambda: {
            "qwen_calls": 0,
            "deepseek_calls": 0,
            "codex_calls": 0,
        }
    )

    def __post_init__(self) -> None:
        if self.status not in VALID_PLAN_STATUSES:
            raise ValueError(
                f"Invalid plan status: {self.status}"
            )


class TaskManager:
    def __init__(self, path: str | None = None):
        default_path = WORKSPACE_DIR / "tasks.json"
        self.path = normalize_path(path) if path else default_path
        self.temporary_path = Path(f"{self.path}.tmp")

    def load_plan(self) -> ProjectPlan | None:
        if not self.path.exists():
            return None

        with self.path.open(
            "r",
            encoding="utf-8",
        ) as plan_file:
            payload = json.load(plan_file)

        payload["tasks"] = [
            Task(**task)
            for task in payload["tasks"]
        ]
        return ProjectPlan(**payload)

    def load(self) -> ProjectPlan | None:
        return self.load_plan()

    def save_plan(self, plan: ProjectPlan) -> None:
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        with self.temporary_path.open(
            "w",
            encoding="utf-8",
        ) as plan_file:
            json.dump(
                asdict(plan),
                plan_file,
                ensure_ascii=False,
                indent=2,
            )
            plan_file.write("\n")
            plan_file.flush()
            os.fsync(plan_file.fileno())

        os.replace(
            self.temporary_path,
            self.path,
        )

    def update_budget_usage(
        self,
        budget_usage: dict[str, int],
    ) -> None:
        plan = self._require_plan()

        required = {
            "qwen_calls",
            "deepseek_calls",
            "codex_calls",
        }

        if set(budget_usage) != required:
            raise ValueError(
                "Invalid plan budget usage fields."
            )

        if any(
            not isinstance(value, int) or value < 0
            for value in budget_usage.values()
        ):
            raise ValueError(
                "Plan budget usage values must be non-negative integers."
            )

        plan.budget_usage = dict(budget_usage)
        self.save_plan(plan)

    def create_plan(
        self,
        goal: str,
        project_root: str,
        tasks: list[Task],
        overwrite: bool = False,
    ) -> ProjectPlan:
        if self.path.exists() and not overwrite:
            raise FileExistsError(
                f"A project plan already exists: {self.path}"
            )
        if not goal.strip():
            raise ValueError("Plan goal cannot be empty.")
        if not tasks:
            raise ValueError("A plan must contain at least one task.")
        task_ids = [task.id for task in tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Task IDs must be unique.")
        task_id_set = set(task_ids)
        for task in tasks:
            for dependency in task.depends_on:
                if dependency not in task_id_set:
                    raise ValueError(
                        f"Task {task.id} depends on unknown task "
                        f"{dependency}."
                    )
                if dependency == task.id:
                    raise ValueError(
                        f"Task {task.id} cannot depend on itself."
                    )
        self._validate_no_dependency_cycles(tasks)

        plan = ProjectPlan(
            version=1,
            goal=goal,
            project_root=str(normalize_path(project_root)),
            status="pending",
            current_task_id=None,
            tasks=tasks,
        )
        self.save_plan(plan)
        return plan

    def _validate_no_dependency_cycles(
        self,
        tasks: list[Task],
    ) -> None:
        dependencies = {
            task.id: task.depends_on
            for task in tasks
        }
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visited:
                return
            if task_id in visiting:
                raise ValueError(
                    "Circular dependency detected involving "
                    f"{task_id}."
                )
            visiting.add(task_id)
            for dependency in dependencies[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in dependencies:
            visit(task_id)

    def next_task(self) -> Task | None:
        plan = self._require_plan()
        if plan.current_task_id is not None:
            current_task = self._find_task(
                plan,
                plan.current_task_id,
            )
            if current_task.status == "running":
                return current_task

        completed_ids = {
            task.id
            for task in plan.tasks
            if task.status == "completed"
        }
        for task in plan.tasks:
            if task.status != "pending":
                continue
            dependencies_satisfied = all(
                dependency in completed_ids
                for dependency in task.depends_on
            )
            if dependencies_satisfied:
                return task
        return None

    def start_task(
        self,
        task_id: str | None = None,
    ) -> Task:
        plan = self._require_plan()
        selected_id = task_id or plan.current_task_id

        if selected_id is None:
            selected = next(
                (
                    task
                    for task in plan.tasks
                    if task.status == "pending"
                ),
                None,
            )
            if selected is None:
                raise ValueError("No pending task is available to start.")
        else:
            selected = self._find_task(plan, selected_id)

        if plan.current_task_id is not None:
            current = self._find_task(
                plan,
                plan.current_task_id,
            )
            if current.status == "running" and current.id != selected.id:
                raise ValueError(
                    f"Task {current.id} is already running."
                )

        if selected.status not in {"pending", "running"}:
            raise ValueError(
                f"Task {selected.id} cannot start from status "
                f"{selected.status}."
            )

        selected.status = "running"
        selected.attempts += 1
        plan.current_task_id = selected.id
        self._update_plan_status(plan)
        self.save_plan(plan)
        return selected

    def complete_task(
        self,
        task_id: str,
        commit_hash: str,
    ) -> Task:
        plan = self._require_plan()
        task = self._current_running_task(
            plan,
            task_id,
        )
        if not commit_hash.strip():
            raise ValueError("Commit hash cannot be empty.")

        task.status = "completed"
        task.commit_hash = commit_hash
        plan.current_task_id = None
        self._update_plan_status(plan)
        self.save_plan(plan)
        return task

    def fail_task(self, task_id: str) -> Task:
        plan = self._require_plan()
        task = self._current_running_task(
            plan,
            task_id,
        )
        task.status = "failed"
        plan.current_task_id = None
        self._update_plan_status(plan)
        self.save_plan(plan)
        return task

    def reset_failed_task(self, task_id: str) -> Task:
        plan = self._require_plan()
        task = self._find_task(
            plan,
            task_id,
        )
        if task.status != "failed":
            raise ValueError(
                f"Task {task.id} cannot be reset from status "
                f"{task.status}."
            )

        task.status = "pending"
        task.commit_hash = None
        plan.current_task_id = None
        self._update_plan_status(plan)
        self.save_plan(plan)
        return task

    def _update_plan_status(
        self,
        plan: ProjectPlan,
    ) -> None:
        if all(
            task.status == "completed"
            for task in plan.tasks
        ):
            plan.status = "completed"
            plan.current_task_id = None
            return

        if any(
            task.status == "running"
            for task in plan.tasks
        ):
            plan.status = "running"
            return

        if any(
            task.status == "failed"
            for task in plan.tasks
        ):
            plan.status = "failed"
            plan.current_task_id = None
            return

        plan.status = "pending"
        plan.current_task_id = None

    def _require_plan(self) -> ProjectPlan:
        plan = self.load_plan()
        if plan is None:
            raise FileNotFoundError(
                f"Task plan does not exist: {self.path}"
            )
        return plan

    @staticmethod
    def _find_task(plan: ProjectPlan, task_id: str) -> Task:
        for task in plan.tasks:
            if task.id == task_id:
                return task
        raise KeyError(f"Unknown task ID: {task_id}")

    def _current_running_task(
        self,
        plan: ProjectPlan,
        task_id: str,
    ) -> Task:
        if plan.current_task_id is None:
            raise ValueError("There is no current task to update.")
        if plan.current_task_id != task_id:
            raise ValueError(
                f"Task {task_id} is not the current task; "
                f"current task is {plan.current_task_id}."
            )
        task = self._find_task(plan, plan.current_task_id)
        if task.status != "running":
            raise ValueError(
                f"Current task {task.id} is not running."
            )
        return task
