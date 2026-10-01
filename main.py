import argparse
import sys

import config
from cli import workflows
from paths import normalize_path
from providers.qwen import run_qwen
from providers.local import run_local
from providers.codex import run_codex
from providers.deepseek import run_deepseek


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--tui"] or (not argv and sys.stdin.isatty() and sys.stdout.isatty()):
        try:
            from cli.tui import RaphaelApp
        except ImportError as error:
            print(f"TUI dependency unavailable: {error}. Install requirements.txt or use --classic.", file=sys.stderr)
            return 1
        RaphaelApp().run()
        return 0
    if not argv or argv == ["--classic"]:
        from cli.raphael import RaphaelCLI

        return RaphaelCLI().run()

    parser = argparse.ArgumentParser(
        description="Run a provider prompt or implement a goal in an explicit project.",
    )

    parser.add_argument(
        "provider",
        nargs="?",
        choices=["local", "qwen", "deepseek", "codex"],
        help="Provider to call directly (qwen is a legacy alias for local).",
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
        from orchestrator.state import StateRecoveryError
        try:
            result = workflows.resume_plan(project_root)
        except (workflows.WorkflowError, StateRecoveryError) as error:
            parser.error(str(error))
        print(f"Plan status: {result.status}")
        return 0 if result.status == "completed" else 1

    if args.goal is not None:
        try:
            result = workflows.run_goal(project_root, args.goal)
        except workflows.WorkflowError as error:
            parser.error(str(error))
        print(f"Plan status: {result.status}")
        return 0 if result.status == "completed" else 1

    if args.provider == "local":
        result = run_local(args.prompt)
    elif args.provider == "qwen":  # Backward-compatible CLI alias.
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
