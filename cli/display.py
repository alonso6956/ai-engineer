"""Terminal presentation only; no persistence or agent execution."""

import os
import shutil
import sys

import config
from orchestrator.budget import BudgetLimits
from orchestrator.state import TaskState
from orchestrator.task_manager import ProjectPlan


LOGO = r"""
 ____      _    ____  _   _    _    _____ _
|  _ \    / \  |  _ \| | | |  / \  | ____| |
| |_) |  / _ \ | |_) | |_| | / _ \ |  _| | |
|  _ <  / ___ \|  __/|  _  |/ ___ \| |___| |___
|_| \_\/_/   \_\_|   |_| |_/_/   \_\_____|_____|
""".strip("\n")

HELP = """/project <path>  Select a Git repository (supports ~, relative paths and spaces)
/project         Show the active project
/new <name> [path]    Create and select a new Git project
/status          Show the saved plan and checkpoint status
/plan            Show the saved plan's tasks
/budget          Show plan usage and engine budget limits
/resume          Resume the active project's saved plan, retrying a failed task
/clear           Clear the terminal and redraw the header
/help            Show these commands
/exit, /quit     Exit RAPHAEL

Type an implementation goal to run the coding agent on the active project.
Ctrl+C interrupts the current operation; /exit or Ctrl+D leaves RAPHAEL."""


def terminal_text(text: str) -> str:
    """Keep prompts and status symbols readable on ASCII-only terminals too."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return text
    except UnicodeEncodeError:
        for symbol, fallback in {"›": ">", "→": "->", "✓": "[done]", "◆": "[run]", "○": "[todo]", "✗": "[fail]"}.items():
            text = text.replace(symbol, fallback)
        return text.encode(encoding, errors="replace").decode(encoding)


def write(text: str = "") -> None:
    print(terminal_text(text))


def divider() -> None:
    write("-" * max(1, min(shutil.get_terminal_size((80, 24)).columns, 64)))


def header(project: str | None) -> None:
    if shutil.get_terminal_size((80, 24)).columns >= max(map(len, LOGO.splitlines())):
        write(LOGO)
    write("RAPHAEL")
    write("AI ENGINEER")
    divider()
    write(f"Project      {project or 'No project selected'}")
    write("Architect    Codex")
    write(f"Workers      {config.LOCAL_MODEL_LABEL} → DeepSeek → Codex")
    write(f"Local Model  {config.LOCAL_MODEL_NAME}")
    write("Status       Ready")
    divider()
    info("Type /help for commands, or select a repository with /project <path>.")


def clear() -> None:
    if sys.stdout.isatty() and os.environ.get("TERM") != "dumb":
        print("\033[2J\033[H", end="", flush=True)
    else:
        divider()


def info(message: str) -> None:
    write(message)


def success(message: str) -> None:
    write(f"OK: {message}")


def error(message: str) -> None:
    write(f"Error: {message}")


def status(project: str | None, plan: ProjectPlan | None, state: TaskState | None) -> None:
    write(f"Project      {project or 'No project selected'}")
    write(f"Plan status  {plan.status if plan else 'No saved plan'}")
    write(f"Current task {(plan.current_task_id or 'None') if plan else 'None'}")
    if plan:
        write(f"Plan project {plan.project_root}")
    if state:
        write(f"Checkpoint   {state.status}")
        write(f"Checkpoint project  {state.project_root}")
        write(f"Checkpoint task     {state.task}")


def plan(plan: ProjectPlan | None) -> None:
    if plan is None:
        info("No saved plan.")
        return
    write(f"Plan project {plan.project_root}")
    write(f"Goal         {plan.goal}")
    write(f"Plan status  {plan.status}")
    markers = {"completed": "✓", "running": "◆", "pending": "○", "failed": "✗"}
    for task in plan.tasks:
        description = " ".join(task.description.split())
        write(f"{markers.get(task.status, '?')} {task.id}  {description}")


def budget(plan: ProjectPlan | None, limits: BudgetLimits) -> None:
    if plan is None:
        info("No saved plan; no recorded budget usage.")
    else:
        write(f"Plan project {plan.project_root}")
    # qwen_calls is retained as the plan's legacy serialized local counter.
    for label, field in (("Local", "qwen_calls"), ("DeepSeek", "deepseek_calls"), ("Codex", "codex_calls")):
        used = plan.budget_usage.get(field, 0) if plan else 0
        limit = getattr(limits, field)
        write(f"{label:<12} {used} / {'unlimited' if limit is None else limit}")
