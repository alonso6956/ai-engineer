"""A persistent, line-oriented coding-agent interface over the existing engine."""

from pathlib import Path
import shlex

from cli import display, workflows
from cli.project import create_project
from orchestrator.budget import BudgetLimits
from orchestrator.state import StateManager
from orchestrator.task_manager import TaskManager
from paths import normalize_path
from tools.git import GitManager


class RaphaelCLI:
    def __init__(self) -> None:
        self.project_root: Path | None = None
        self.task_manager = TaskManager()
        self.state_manager = StateManager()

    def run(self) -> int:
        display.header(self._project_label())
        while True:
            try:
                line = input(display.terminal_text("raphael › "))
            except EOFError:
                display.info("\nGoodbye.")
                return 0
            except KeyboardInterrupt:
                display.info("\nUse /exit or Ctrl+D to leave RAPHAEL.")
                continue

            try:
                if not self.dispatch(line):
                    display.info("Goodbye.")
                    return 0
            except KeyboardInterrupt:
                display.info("\nExecution interrupted. Use /status to inspect state and /resume to continue a saved plan.")
            except Exception as error:
                display.error(f"{type(error).__name__}: {error}")

    def _project_label(self) -> str | None:
        return str(self.project_root) if self.project_root is not None else None

    def _require_project(self) -> Path:
        if self.project_root is None:
            raise ValueError("Select a project first with /project <path>.")
        return self.project_root

    def _select_project(self, argument: str) -> None:
        if not argument:
            display.info(self._project_label() or "No project selected. Use /project <path>.")
            return
        # Treat unquoted spaces as part of the path; also accept shell-style quotes.
        if argument.startswith(('"', "'")):
            parts = shlex.split(argument)
            if len(parts) != 1:
                raise ValueError("Usage: /project <path>")
            argument = parts[0]
        if not argument:
            raise ValueError("Project path cannot be empty.")
        self._activate_project(normalize_path(argument))

    def _activate_project(self, project: Path) -> None:
        """One selection path for /project and successfully created repositories."""
        if not project.exists():
            raise FileNotFoundError(f"Project root does not exist: {project}")
        if not project.is_dir():
            raise NotADirectoryError(f"Project root is not a directory: {project}")
        GitManager(str(project))  # Read-only repository validation.
        self.project_root = project
        display.success(f"Project changed to:\n{project}")

    def _new_project(self, argument: str) -> None:
        usage = "Usage: /new <project-name> [parent-path]"
        try:
            lexer = shlex.shlex(argument, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = ""
            # Preserve backslashes so invalid names are rejected, not sanitized.
            # Parent paths with spaces can be quoted, as in /project.
            lexer.escape = ""
            arguments = list(lexer)
        except ValueError as error:
            raise ValueError(usage) from error
        if len(arguments) not in {1, 2}:
            raise ValueError(usage)
        display.info("Creating project...")
        project = create_project(
            arguments[0],
            arguments[1] if len(arguments) == 2 else None,
            progress=display.success,
        )
        self._activate_project(project)
        display.info(f"Project      {project}\nStatus       Ready")

    def dispatch(self, line: str) -> bool:
        """Process one line; False requests an explicit exit."""
        line = line.strip()
        if not line:
            return True
        if not line.startswith("/"):
            result = workflows.run_goal(self._require_project(), line)
            display.info(f"Plan status: {result.status}")
            return True

        parts = line.split(maxsplit=1)
        command = parts[0]
        argument = parts[1].strip() if len(parts) == 2 else ""
        if command == "/project":
            self._select_project(argument)
            return True
        if command == "/new":
            self._new_project(argument)
            return True
        if command not in {"/status", "/plan", "/budget", "/resume", "/clear", "/help", "/exit", "/quit"}:
            raise ValueError(f"Unknown command: {command}. Type /help for commands.")
        if argument:
            raise ValueError(f"{command} does not accept arguments.")

        if command in {"/exit", "/quit"}:
            return False
        if command == "/help":
            display.info(display.HELP)
        elif command == "/clear":
            display.clear()
            display.header(self._project_label())
        elif command == "/status":
            display.status(self._project_label(), self.task_manager.load_plan(), self.state_manager.load())
        elif command == "/plan":
            display.plan(self.task_manager.load_plan())
        elif command == "/budget":
            display.budget(self.task_manager.load_plan(), BudgetLimits())
        elif command == "/resume":
            result = workflows.resume_plan(self._require_project())
            display.info(f"Plan status: {result.status}")
        return True
