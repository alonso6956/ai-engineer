import io
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import main
from cli import display, workflows
from cli.raphael import RaphaelCLI
from orchestrator.architect import ArchitecturePlan, CodexArchitect
from orchestrator.budget import BudgetLimits
from orchestrator.plan_runner import PlanRunner
from orchestrator.state import StateManager, TaskState
from orchestrator.task_manager import ProjectPlan, Task, TaskManager
from paths import BASE_DIR


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    for module in ("orchestrator.task_manager", "orchestrator.state", "orchestrator.scheduler"):
        monkeypatch.setattr(f"{module}.WORKSPACE_DIR", root)
    return root


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project with spaces"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def run_shell(monkeypatch, lines):
    reader = Mock(side_effect=lines)
    monkeypatch.setattr("builtins.input", reader)
    shell = RaphaelCLI()
    assert shell.run() == 0
    return shell, reader


def save_plan(project, status="pending"):
    plan = ProjectPlan(
        version=1, goal="Implement a parser", project_root=str(project), status=status,
        current_task_id="task-002" if status == "running" else None,
        tasks=[
            Task("task-001", "Implement parser", "Parser", status="completed"),
            Task("task-002", "Add validation", "Validation", status=status),
            Task("task-003", "Add tests", "Tests"),
        ],
        budget_usage={"qwen_calls": 7, "deepseek_calls": 2, "codex_calls": 1},
    )
    TaskManager().save_plan(plan)
    return plan


def save_state(project):
    state = TaskState(
        version=1, status="interrupted", task="Add validation", project_root=str(project),
        commit_message="Validation", starting_head="saved-head", current_provider=None,
        qwen_failures=1, deepseek_failures=0, codex_failures=0,
        attempts=[], last_candidate=None, last_review=None,
        budget_usage={"qwen_calls": 99, "deepseek_calls": 99, "codex_calls": 99},
    )
    StateManager().save(state)
    return state


def test_no_arguments_start_raphael(monkeypatch, capsys):
    reader = Mock(return_value="/exit")
    monkeypatch.setattr("builtins.input", reader)
    assert main.main([]) == 0
    output = capsys.readouterr().out
    for text in ("RAPHAEL", "AI ENGINEER", "No project selected", "Architect    Codex", "Local → DeepSeek → Codex", "Local Model", "Ready"):
        assert text in output
    reader.assert_called_once_with("raphael › ")


@pytest.mark.parametrize("form", ["absolute", "relative", "home", "quoted"])
def test_project_selection_is_read_only(project, tmp_path, monkeypatch, workspace, form, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    argument = {
        "absolute": str(project), "relative": project.name,
        "home": "~/" + project.name, "quoted": f'"{project}"',
    }[form]
    before = {str(p.relative_to(project)): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    shell, _ = run_shell(monkeypatch, [f"/project {argument}", "/project", "/exit"])
    assert shell.project_root == project.resolve()
    assert "Project changed to:" in capsys.readouterr().out
    assert {str(p.relative_to(project)): p.read_bytes() for p in project.rglob("*") if p.is_file()} == before
    assert not workspace.exists()


def test_invalid_commands_return_to_prompt_and_keep_selection(project, tmp_path, monkeypatch, capsys):
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    file = tmp_path / "file.txt"
    file.touch()
    commands = [
        f"/project {project}", f"/project {ordinary}", f"/project {file}",
        f"/project {tmp_path / 'missing'}", '/project "unterminated',
        "/unknown", "/status extra", "/exit extra", "/project", "/quit",
    ]
    shell, reader = run_shell(monkeypatch, commands)
    assert shell.project_root == project.resolve()
    assert reader.call_count == len(commands)
    output = capsys.readouterr().out
    for text in ("No es un repositorio Git", "not a directory", "does not exist", "Unknown command", "does not accept arguments"):
        assert text in output
    assert "Traceback" not in output


def test_missing_project_and_state_are_readable(monkeypatch, capsys, workspace):
    with patch.object(workflows, "run_goal") as goal, patch.object(workflows, "resume_plan") as resume:
        run_shell(monkeypatch, ["", "/project", "/status", "/plan", "/budget", "Implement parser", "/resume", "/help", "/exit"])
    goal.assert_not_called()
    resume.assert_not_called()
    output = capsys.readouterr().out
    assert "No saved plan" in output
    assert "Select a project first" in output
    assert "Local        0 / unlimited" in output
    assert "/resume" in output
    assert not workspace.exists()


def test_inspection_and_clear_preserve_state(project, monkeypatch, capsys):
    saved = save_plan(project, status="running")
    save_state(project)
    plan_path, state_path = TaskManager().path, StateManager().path
    original_plan, original_state = plan_path.read_bytes(), state_path.read_bytes()
    shell, _ = run_shell(monkeypatch, [f"/project {project}", "/status", "/plan", "/budget", "/clear", "/project", "/exit"])
    assert shell.project_root == project
    assert plan_path.read_bytes() == original_plan
    assert state_path.read_bytes() == original_state
    output = capsys.readouterr().out
    for text in ("Plan status  running", "Current task task-002", "Checkpoint   interrupted",
                 "✓ task-001", "◆ task-002", "○ task-003", "7 / unlimited", "2 / 3", "1 / 1"):
        assert text in output
    assert "99 /" not in output  # Usage comes from the plan, not the task checkpoint.
    assert output.count("RAPHAEL\n") == 2
    assert TaskManager().load_plan() == saved


def test_different_saved_project_is_labeled(project, tmp_path, monkeypatch, capsys):
    other = tmp_path / "other-project"
    save_plan(other)
    run_shell(monkeypatch, [f"/project {project}", "/status", "/plan", "/budget", "/exit"])
    output = capsys.readouterr().out
    assert f"Project      {project}" in output
    assert output.count(f"Plan project {other}") == 3


def test_budget_uses_engine_limits(project, monkeypatch, capsys):
    save_plan(project)
    limits = BudgetLimits(qwen_calls=10, deepseek_calls=5, codex_calls=None)
    with patch("cli.raphael.BudgetLimits", return_value=limits):
        run_shell(monkeypatch, ["/budget", "/exit"])
    output = capsys.readouterr().out
    assert "7 / 10" in output
    assert "2 / 5" in output
    assert "1 / unlimited" in output


def test_goal_and_resume_dispatch_match_argparse(project, monkeypatch):
    result = SimpleNamespace(status="completed")
    with patch.object(workflows, "run_goal", return_value=result) as goal:
        with patch.object(workflows, "resume_plan", return_value=result) as resume:
            run_shell(monkeypatch, [f"/project {project}", "Implement parser", "/resume", "/exit"])
            assert main.main(["--project", str(project), "--goal", "Implement parser"]) == 0
            assert main.main(["--project", str(project), "--resume"]) == 0
    assert goal.call_count == resume.call_count == 2
    assert goal.call_args_list[0] == goal.call_args_list[1]
    assert resume.call_args_list[0] == resume.call_args_list[1]


def test_shared_resume_retries_failed_plan(project):
    saved = save_plan(project, status="failed")
    with patch.object(PlanRunner, "run", return_value=saved) as run:
        assert workflows.resume_plan(project) == saved
    run.assert_called_once_with(retry_failed=True)


def test_shared_goal_preserves_architect_tasks_and_completed_overwrite(project):
    save_plan(project, status="completed")
    task = Task("task-001", "Work", "Commit", acceptance_criteria=["Works"], allowed_test_files=["test_feature.py"])
    architecture = ArchitecturePlan("Summary", [task])
    with patch.object(CodexArchitect, "create_plan", return_value=architecture) as architect:
        with patch.object(PlanRunner, "run", return_value=SimpleNamespace(status="completed")):
            workflows.run_goal(project, "New goal")
    architect.assert_called_once_with("New goal")
    assert TaskManager().load_plan().tasks == [task]


def test_goal_does_not_overwrite_unfinished_plan(project, monkeypatch, capsys):
    save_plan(project, status="running")
    before = TaskManager().path.read_bytes()
    with patch.object(CodexArchitect, "create_plan") as architect:
        run_shell(monkeypatch, [f"/project {project}", "New goal", "/exit"])
    assert TaskManager().path.read_bytes() == before
    architect.assert_not_called()
    assert "unfinished plan exists" in capsys.readouterr().out


def test_resume_errors_do_not_exit_shell(project, tmp_path, monkeypatch, capsys):
    run_shell(monkeypatch, [f"/project {project}", "/resume", "/exit"])
    assert "No saved plan exists" in capsys.readouterr().out
    save_plan(tmp_path / "other-project")
    with patch.object(PlanRunner, "run") as run:
        run_shell(monkeypatch, [f"/project {project}", "/resume", "/exit"])
    run.assert_not_called()
    assert "different project" in capsys.readouterr().out


def test_idle_and_engine_interrupts_keep_shell_alive(project, monkeypatch, capsys):
    with patch.object(workflows, "run_goal", side_effect=KeyboardInterrupt):
        shell, reader = run_shell(monkeypatch, [KeyboardInterrupt(), f"/project {project}", "Goal", "/status", "/exit"])
    assert reader.call_count == 5
    assert shell.project_root == project
    output = capsys.readouterr().out
    assert "Use /exit" in output
    assert "Execution interrupted" in output
    assert "Traceback" not in output


def test_unexpected_engine_error_is_reported(project, monkeypatch, capsys):
    with patch.object(workflows, "run_goal", side_effect=LookupError("diagnostic detail")):
        run_shell(monkeypatch, [f"/project {project}", "Goal", "/help", "/exit"])
    assert "LookupError: diagnostic detail" in capsys.readouterr().out


def test_malformed_saved_json_is_reported_without_overwriting(project, monkeypatch, capsys):
    path = TaskManager().path
    path.parent.mkdir()
    path.write_text("not json", encoding="utf-8")
    run_shell(monkeypatch, ["/status", "/help", "/exit"])
    assert "JSONDecodeError" in capsys.readouterr().out
    assert path.read_text() == "not json"


def test_eof_exits_cleanly(monkeypatch, capsys):
    run_shell(monkeypatch, [EOFError()])
    assert "Goodbye" in capsys.readouterr().out


def test_display_handles_narrow_ascii_terminal_without_ansi(monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="ascii")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("COLUMNS", "8")
    display.header(None)
    display.clear()
    display.plan(ProjectPlan(1, "Goal", ".", tasks=[Task("task-001", "Work", "Commit", status="completed")]))
    assert display.terminal_text("raphael › ") == "raphael > "
    stream.flush()
    output = raw.getvalue().decode("ascii")
    assert "RAPHAEL" in output
    assert "[done] task-001" in output
    assert "\x1b" not in output


def test_clear_uses_ansi_on_a_terminal(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("TERM", "xterm")
    display.clear()
    assert capsys.readouterr().out == "\033[2J\033[H"


def test_actual_entrypoint_starts_and_exits_from_other_cwd(tmp_path):
    result = subprocess.run(
        [sys.executable, str(BASE_DIR / "main.py")], input="/exit\n",
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "RAPHAEL" in result.stdout
    assert "raphael › " in result.stdout
    assert "Goodbye" in result.stdout
    assert "\x1b" not in result.stdout
