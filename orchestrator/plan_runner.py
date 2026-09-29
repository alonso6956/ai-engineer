from paths import normalize_path

from orchestrator.budget import BudgetLimits
from orchestrator.scheduler import Scheduler
from orchestrator.state import StateRecoveryError
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

    def run(self, retry_failed: bool = False) -> ProjectPlan:
        plan = self.task_manager.load()
        if plan is None:
            raise RuntimeError("No project plan exists.")

        project_root = normalize_path(plan.project_root)
        if not project_root.exists() or not project_root.is_dir():
            raise RuntimeError(
                f"Project root does not exist: {project_root}"
            )

        while True:
            plan = self.task_manager.load()
            if plan is None:
                raise RuntimeError("Project plan disappeared while running.")
            if plan.status == "failed":
                if not retry_failed:
                    return plan

                failed_tasks = [
                    task
                    for task in plan.tasks
                    if task.status == "failed"
                ]

                if len(failed_tasks) != 1:
                    raise StateRecoveryError(
                        "Failed plan recovery requires exactly one failed task."
                    )

                failed_task = failed_tasks[0]

                self.task_manager.reset_failed_task(
                    failed_task.id
                )

                plan = self.task_manager.load()

                if plan is None:
                    raise StateRecoveryError(
                        "Project plan disappeared during failed-task recovery."
                    )

            task = self.task_manager.next_task()
            if task is None:
                return plan
            if task.status == "failed":
                return plan

            scheduler = Scheduler(
                project_root=str(project_root),
                max_worker_steps=self.max_worker_steps,
                budget_limits=self.budget_limits,
                initial_budget_usage=plan.budget_usage,
            )
            checkpoint = scheduler.state_manager.get_incomplete_task()
            completed_checkpoint = scheduler.state_manager.get_completed_task()
            if task.status == "running":
                if checkpoint is None:
                    if completed_checkpoint is None:
                        raise StateRecoveryError(
                            f"Task {task.id} is running but has no recoverable checkpoint."
                        )

                    if (
                        normalize_path(completed_checkpoint.project_root) != project_root
                        or completed_checkpoint.task != task.description
                        or completed_checkpoint.commit_message != task.commit_message
                        or completed_checkpoint.acceptance_criteria != task.acceptance_criteria
                        or completed_checkpoint.allowed_test_files != task.allowed_test_files
                    ):
                        raise StateRecoveryError(
                            "Completed checkpoint does not match the current plan task."
                        )

                    completed_commit_hash = completed_checkpoint.commit_hash
                    if not completed_commit_hash:
                        raise StateRecoveryError(
                            "Completed checkpoint does not contain a commit hash."
                        )

                    head_result = scheduler.git.head()
                    if not head_result.success:
                        raise StateRecoveryError(
                            "Could not read repository HEAD during reconciliation."
                        )

                    if head_result.stdout.strip() != completed_commit_hash:
                        raise StateRecoveryError(
                            "Completed checkpoint commit does not match repository HEAD."
                        )

                    self.task_manager.update_budget_usage(
                        completed_checkpoint.budget_usage
                    )
                    self.task_manager.complete_task(
                        task_id=task.id,
                        commit_hash=completed_commit_hash,
                    )
                    continue
                if (
                    normalize_path(checkpoint.project_root) != project_root
                    or checkpoint.task != task.description
                    or checkpoint.commit_message != task.commit_message
                    or checkpoint.acceptance_criteria != task.acceptance_criteria
                    or checkpoint.allowed_test_files != task.allowed_test_files
                ):
                    raise StateRecoveryError(
                        "Saved checkpoint does not match the current plan task."
                    )
                result = scheduler.resume_task()
            else:
                if checkpoint is not None:
                    raise StateRecoveryError(
                        "An incomplete checkpoint exists; refusing to overwrite it "
                        "with a new plan task."
                    )
                task = self.task_manager.start_task(task.id)
                result = scheduler.run_task(
                    task=task.description,
                    commit_message=task.commit_message,
                    acceptance_criteria=task.acceptance_criteria,
                    allowed_test_files=task.allowed_test_files,
                )

            scheduler_state = scheduler.state_manager.load()
            if scheduler_state is not None:
                self.task_manager.update_budget_usage(
                    scheduler_state.budget_usage
                )

            if not result.success or result.commit_hash is None:
                self.task_manager.fail_task(task.id)
                return self.task_manager.load()

            self.task_manager.complete_task(
                task_id=task.id,
                commit_hash=result.commit_hash,
            )
