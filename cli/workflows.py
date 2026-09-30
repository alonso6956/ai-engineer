"""Goal and recovery entry points shared by both terminal interfaces."""

from pathlib import Path

from paths import normalize_path
from orchestrator.task_manager import ProjectPlan, TaskManager


class WorkflowError(ValueError):
    """An invocation cannot proceed with the current saved plan."""


def run_goal(project_root: str | Path, goal: str) -> ProjectPlan:
    from orchestrator.architect import CodexArchitect
    from orchestrator.plan_runner import PlanRunner
    from orchestrator.scheduler import Scheduler

    project_root = normalize_path(project_root)
    task_manager = TaskManager()
    existing_plan = task_manager.load_plan()
    if existing_plan is not None and existing_plan.status != "completed":
        raise WorkflowError(
            f"An unfinished plan exists at {task_manager.path}. "
            "Use --project PROJECT --resume (or /resume in RAPHAEL) to continue it."
        )
    scheduler = Scheduler(str(project_root))
    if scheduler.state_manager.has_incomplete_task():
        raise WorkflowError(
            "An interrupted task exists. Recover it with "
            "Scheduler(project_root).resume_task() before starting a new goal."
        )
    architecture = CodexArchitect(str(project_root)).create_plan(goal)
    task_manager.create_plan(
        goal=goal,
        project_root=str(project_root),
        tasks=architecture.tasks,
        overwrite=(existing_plan is not None and existing_plan.status == "completed"),
    )
    print(architecture.summary)
    return PlanRunner(task_manager).run()


def resume_plan(project_root: str | Path) -> ProjectPlan:
    from orchestrator.plan_runner import PlanRunner

    project_root = normalize_path(project_root)
    task_manager = TaskManager()
    plan = task_manager.load_plan()
    if plan is None:
        raise WorkflowError(f"No saved plan exists at {task_manager.path}.")
    if normalize_path(plan.project_root) != project_root:
        raise WorkflowError("Saved plan belongs to a different project.")
    return PlanRunner(task_manager).run(retry_failed=True)
