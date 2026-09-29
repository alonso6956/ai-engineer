import argparse
import sys

import config
from paths import normalize_path
from providers.qwen import run_qwen
from providers.codex import run_codex
from providers.deepseek import run_deepseek


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a provider prompt or implement a goal in an explicit project.",
    )

    parser.add_argument(
        "provider",
        nargs="?",
        choices=["qwen", "deepseek", "codex"],
    )

    parser.add_argument("prompt", nargs="?")
    parser.add_argument("--project", help="Target repository (absolute, relative, or ~/path).")
    parser.add_argument("--goal", help="Create and execute an implementation plan.")
    parser.add_argument("--resume", action="store_true", help="Resume the saved project plan.")

    args = parser.parse_args(argv)

    if args.resume:
        if args.goal is not None or args.provider is not None or args.prompt is not None:
            parser.error("--resume cannot be combined with --goal or a provider prompt.")
        if args.project is None:
            parser.error("--project is required with --resume.")
    elif args.goal is not None:
        if args.provider is not None or args.prompt is not None:
            parser.error("--goal cannot be combined with a provider prompt.")
        if not args.goal.strip():
            parser.error("--goal cannot be empty.")
        if args.project is None:
            parser.error("--project is required with --goal.")
    elif args.provider is None or args.prompt is None:
        parser.error("Provide --project and --goal, or a provider and prompt.")

    if args.provider == "codex" and args.project is None:
        parser.error("--project is required for the Codex provider.")

    project_root = None
    if args.project is not None:
        try:
            project_root = normalize_path(args.project)
        except (OSError, RuntimeError, ValueError) as error:
            parser.error(f"Invalid project root: {error}")
        if not project_root.exists():
            parser.error(f"Project root does not exist: {project_root}")
        if not project_root.is_dir():
            parser.error(f"Project root is not a directory: {project_root}")

    if args.resume:
        from orchestrator.plan_runner import PlanRunner
        from orchestrator.state import StateRecoveryError
        from orchestrator.task_manager import TaskManager

        task_manager = TaskManager()
        plan = task_manager.load_plan()
        if plan is None:
            parser.error(f"No saved plan exists at {task_manager.path}.")
        if normalize_path(plan.project_root) != project_root:
            parser.error("Saved plan belongs to a different project.")
        try:
            result = PlanRunner(task_manager).run(
                retry_failed=True
            )
        except StateRecoveryError as error:
            parser.error(str(error))
        print(f"Plan status: {result.status}")
        return 0 if result.status == "completed" else 1

    if args.goal is not None:
        from orchestrator.architect import CodexArchitect
        from orchestrator.plan_runner import PlanRunner
        from orchestrator.scheduler import Scheduler
        from orchestrator.task_manager import TaskManager

        task_manager = TaskManager()
        existing_plan = task_manager.load_plan()
        if existing_plan is not None and existing_plan.status != "completed":
            parser.error(
                f"An unfinished plan exists at {task_manager.path}. "
                "Use --project PROJECT --resume to continue it."
            )
        scheduler = Scheduler(str(project_root))
        if scheduler.state_manager.has_incomplete_task():
            parser.error(
                "An interrupted task exists. Recover it with "
                "Scheduler(project_root).resume_task() before starting a new goal."
            )
        architecture = CodexArchitect(str(project_root)).create_plan(args.goal)
        task_manager.create_plan(
            goal=args.goal,
            project_root=str(project_root),
            tasks=architecture.tasks,
            overwrite=(
                existing_plan is not None
                and existing_plan.status == "completed"
            ),
        )
        print(architecture.summary)
        result = PlanRunner(task_manager).run()
        print(f"Plan status: {result.status}")
        return 0 if result.status == "completed" else 1

    if args.provider == "qwen":
        result = run_qwen(args.prompt)
    elif args.provider == "deepseek":
        result = run_deepseek(args.prompt)
    elif args.provider == "codex":
        result = run_codex(
            args.prompt,
            cwd=str(project_root),
        )

    print(result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nExecution interrupted. Use --project PROJECT --resume for a saved plan.", file=sys.stderr)
        raise SystemExit(130)
