from pathlib import Path

from orchestrator.budget import BudgetLimits
from orchestrator.scheduler import Scheduler
from orchestrator.task_manager import ProjectPlan, TaskManager


class PlanRunner:
    def __init__(
        self,
        task_manager: TaskManager,
        max_worker_steps: int = 12,
        budget_limits: BudgetLimits | None = None,
    ):
        self.task_manager = task_manager
        self.max_worker_steps = max_worker_steps
        self.budget_limits = budget_limits

    def run(self) -> ProjectPlan:
        plan = self.task_manager.load()
        if plan is None:
            raise RuntimeError("No project plan exists.")

        project_root = Path(plan.project_root).resolve()
        if not project_root.exists() or not project_root.is_dir():
            raise RuntimeError(
                f"Project root does not exist: {project_root}"
            )

        while True:
            plan = self.task_manager.load()
            if plan is None:
                raise RuntimeError("Project plan disappeared while running.")
            if plan.status == "failed":
                return plan

            task = self.task_manager.next_task()
            if task is None:
                return plan
            if task.status == "failed":
                return plan

            task = self.task_manager.start_task(task.id)
            scheduler = Scheduler(
                project_root=str(project_root),
                max_worker_steps=self.max_worker_steps,
                budget_limits=self.budget_limits,
            )
            result = scheduler.run_task(
                task=task.description,
                commit_message=task.commit_message,
            )

            if not result.success or result.commit_hash is None:
                self.task_manager.fail_task(task.id)
                return self.task_manager.load()

            self.task_manager.complete_task(
                task_id=task.id,
                commit_hash=result.commit_hash,
            )