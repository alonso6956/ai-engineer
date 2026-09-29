import json
import os
import runpy
import subprocess
import sys
from unittest.mock import patch

import pytest

import main
from orchestrator.architect import ArchitecturePlan, ArchitectError, CodexArchitect
from orchestrator.codex_worker import CodexWorker
from orchestrator.local_worker import LocalWorker
from orchestrator.plan_runner import PlanRunner
from orchestrator.scheduler import Scheduler, TaskResult
from orchestrator.state import StateManager, StateRecoveryError
from orchestrator.task_manager import Task, TaskManager
from paths import BASE_DIR, WORKSPACE_DIR
from providers.codex import run_codex
from tools.filesystem import FileSystem
from tools.git import GitManager
from tools.tests import TestRunner as ProjectTestRunner


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "repositories" / "project with spaces"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "example.txt").write_text("Initial content\n", encoding="utf-8")
    subprocess.run(["git", "add", "example.txt"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Path Test", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm", "Initial"],
        cwd=root, check=True,
    )
    return root.resolve()


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "internal workspace"
    monkeypatch.setattr("orchestrator.task_manager.WORKSPACE_DIR", workspace)
    monkeypatch.setattr("orchestrator.scheduler.WORKSPACE_DIR", workspace)
    return workspace


@pytest.mark.parametrize("form", ["absolute", "relative", "home"])
def test_project_paths_across_components(project, tmp_path, monkeypatch, form):
    caller = tmp_path / "unrelated caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("HOME", str(tmp_path))
    supplied = {
        "absolute": str(project),
        "relative": "../repositories/project with spaces",
        "home": "~/repositories/project with spaces",
    }[form]

    assert CodexArchitect(supplied).project_root == project
    for component in (FileSystem, GitManager, ProjectTestRunner):
        assert component(supplied).root == project
    worker = LocalWorker(supplied)
    assert worker.fs.root == worker.git.root == worker.tests.root == project
    for component in (CodexWorker, Scheduler):
        instance = component(supplied)
        assert instance.project_root == str(project)
        assert instance.fs.root == instance.git.root == project

    manager = TaskManager(str(tmp_path / "plans" / "tasks.json"))
    manager.create_plan("Goal", supplied, [Task("task-001", "Work", "Implement")])
    monkeypatch.chdir(tmp_path)
    assert manager.load_plan().project_root == str(project)


def test_workspace_defaults_in_fresh_process_from_other_cwd(project, tmp_path):
    # A fresh import catches import-time dependencies on the caller's CWD too.
    code = """
import json, sys
sys.path.insert(0, sys.argv[1])
import config
from orchestrator.scheduler import Scheduler
from orchestrator.task_manager import TaskManager
print(json.dumps([str(config.BASE_DIR), str(config.WORKSPACE_DIR),
                  str(TaskManager().path),
                  str(Scheduler(sys.argv[2]).state_manager.path)]))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(BASE_DIR), str(project)],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout) == [
        str(BASE_DIR), str(WORKSPACE_DIR),
        str(WORKSPACE_DIR / "tasks.json"), str(WORKSPACE_DIR / "state.json"),
    ]
    assert not (tmp_path / "workspace").exists()


@pytest.mark.parametrize("form", ["relative", "home"])
def test_custom_persistence_paths_stay_anchored(tmp_path, monkeypatch, form):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    prefix = "~/" if form == "home" else ""
    tasks = TaskManager(prefix + "stored/tasks.json")
    state = StateManager(prefix + "stored/state.json")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert tasks.path == tmp_path / "stored/tasks.json"
    assert state.path == tmp_path / "stored/state.json"
    assert tasks.temporary_path == tmp_path / "stored/tasks.json.tmp"
    assert state.temporary_path == tmp_path / "stored/state.json.tmp"


def test_state_manager_default_path_ignores_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert StateManager().path == WORKSPACE_DIR / "state.json"
    assert StateManager(None).path == WORKSPACE_DIR / "state.json"


def test_default_state_manager_load_and_clear(project, tmp_path, monkeypatch):
    workspace = tmp_path / "stored"
    saved = save_interrupted_task(project, workspace)
    expected = saved.load()
    saved.temporary_path.write_text("unfinished write", encoding="utf-8")
    tasks_file = workspace / "tasks.json"
    tasks_file.write_text("preserve plan", encoding="utf-8")
    monkeypatch.setattr("orchestrator.state.WORKSPACE_DIR", workspace)
    monkeypatch.chdir(tmp_path)

    manager = StateManager()
    assert manager.load() == expected
    manager.clear()
    assert manager.load() is None
    assert not manager.temporary_path.exists()
    assert tasks_file.read_text(encoding="utf-8") == "preserve plan"
    manager.clear()  # Clearing an absent checkpoint remains safe.


@pytest.mark.parametrize("component, error", [
    (CodexArchitect, ArchitectError), (FileSystem, FileNotFoundError),
    (GitManager, FileNotFoundError), (ProjectTestRunner, FileNotFoundError),
    (Scheduler, FileNotFoundError), (CodexWorker, FileNotFoundError),
])
def test_missing_project_is_clear(tmp_path, component, error):
    missing = tmp_path / "missing-project"
    with pytest.raises(error, match="missing-project"):
        component(str(missing))


def test_architect_rejects_file(tmp_path):
    file = tmp_path / "file.txt"
    file.touch()
    with pytest.raises(ArchitectError, match="not a directory"):
        CodexArchitect(str(file))


@pytest.mark.parametrize("escape", ["relative", "absolute", "symlink"])
def test_filesystem_and_test_targets_cannot_escape(project, tmp_path, escape):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "data.txt"
    secret.write_text("unchanged", encoding="utf-8")
    if escape == "symlink":
        (project / "escape").symlink_to(outside, target_is_directory=True)
        file_path, directory = "escape/data.txt", "escape"
    elif escape == "relative":
        file_path = os.path.relpath(secret, project)
        directory = os.path.relpath(outside, project)
    else:
        file_path, directory = str(secret), str(outside)

    fs = FileSystem(str(project))
    for operation in (
        lambda: fs.read_file(file_path),
        lambda: fs.write_file(file_path, "overwrite"),
        lambda: fs.delete_file(file_path),
        lambda: fs.file_exists(file_path),
        lambda: fs.list_files(directory),
        lambda: fs.tree(directory),
        lambda: ProjectTestRunner(str(project)).run_pytest(file_path),
    ):
        with pytest.raises(PermissionError):
            operation()
    assert secret.read_text(encoding="utf-8") == "unchanged"


def test_internal_files_and_write_protections(project):
    fs = FileSystem(str(project))
    fs.write_file("src/example.py", "value = 1\n")
    assert fs.read_file("src/example.py") == "value = 1\n"
    for target in ("test_example.py", "pytest.ini", ".git/config"):
        with pytest.raises(PermissionError):
            fs.write_file(target, "blocked")


def save_interrupted_task(project, workspace):
    scheduler = Scheduler(str(project))
    scheduler.state_manager = StateManager(str(workspace / "state.json"))
    # Persist the real initial checkpoint without contacting a worker.
    with patch.object(scheduler, "_run_loop"):
        scheduler.run_task("Implement feature", "Feature")
    return scheduler.state_manager


def test_checkpoint_recovers_after_cwd_change(project, tmp_path, monkeypatch):
    monkeypatch.chdir(project.parent)
    scheduler = Scheduler(project.name)
    scheduler.state_manager = StateManager(str(tmp_path / "stored/state.json"))
    with patch.object(scheduler, "_run_loop"):
        scheduler.run_task("Implement feature", "Feature")
    assert scheduler.state_manager.load().project_root == str(project)
    monkeypatch.chdir(tmp_path)
    recovered = Scheduler(str(project))
    recovered.state_manager = StateManager(str(scheduler.state_manager.path))
    with patch.object(recovered, "_run_loop", return_value="resumed") as loop:
        assert recovered.resume_task() == "resumed"
    assert loop.call_args.kwargs["state"].starting_head == GitManager(str(project)).head().stdout.strip()


@pytest.mark.parametrize("mismatch", ["project", "head"])
def test_recovery_rejects_mismatch_before_rollback(project, tmp_path, mismatch):
    manager = save_interrupted_task(project, tmp_path / "stored")
    state = manager.load()
    if mismatch == "project":
        state.project_root = str(tmp_path)
        message = "different project"
    else:
        state.starting_head = "0" * 40
        message = "HEAD differs"
    manager.save(state)
    scheduler = Scheduler(str(project))
    scheduler.state_manager = manager
    with patch.object(scheduler, "_rollback") as rollback:
        with pytest.raises(StateRecoveryError, match=message):
            scheduler.resume_task()
    rollback.assert_not_called()


def test_plan_runner_accepts_home_path(project, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    manager = TaskManager(str(tmp_path / "stored/tasks.json"))
    plan = manager.create_plan("Goal", str(project), [Task("task-001", "Work", "Commit")])
    # Exercise loading a supplied plan as well as create_plan's normalization.
    plan.project_root = "~/repositories/project with spaces"
    manager.save_plan(plan)
    result = TaskResult(True, "Done", None, None, "abc123")
    with patch("orchestrator.plan_runner.Scheduler") as scheduler:
        scheduler.return_value.state_manager.get_incomplete_task.return_value = None
        scheduler.return_value.run_task.return_value = result
        assert PlanRunner(manager).run().status == "completed"
        assert scheduler.call_args.kwargs["project_root"] == str(project)


def test_codex_subprocesses_use_normalized_cwd(project, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    supplied = "~/repositories/project with spaces"
    response = json.dumps({"summary": "Plan", "tasks": [{
        "id": "task-001", "description": "Work", "commit_message": "Commit",
        "depends_on": [], "acceptance_criteria": ["Works"],
    }]})
    with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, response, "")) as run:
        CodexArchitect(supplied).create_plan("Goal")
        assert run.call_args.kwargs["cwd"] == str(project)
        assert "read-only" in run.call_args.args[0]
        run_codex("Prompt", cwd=supplied)
        assert run.call_args.kwargs["cwd"] == str(project)


def test_pytest_runs_in_project_from_other_cwd(project, tmp_path, monkeypatch):
    (project / "test_sample.py").write_text(
        "from pathlib import Path\ndef test_cwd():\n    assert Path('test_sample.py').is_file()\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    result = ProjectTestRunner(str(project)).run_pytest("test_sample.py")
    assert result.success, str(result)
    assert "1 passed" in result.stdout


def test_goal_cli_creates_and_executes_plan(project, tmp_path, monkeypatch, isolated_workspace):
    monkeypatch.chdir(tmp_path)
    architecture = ArchitecturePlan("Plan", [Task("task-001", "Work", "Commit")])
    result = TaskResult(True, "Done", None, None, "abc123")
    with patch.object(CodexArchitect, "create_plan", return_value=architecture) as architect:
        with patch.object(Scheduler, "run_task", return_value=result) as run:
            assert main.main(["--project", os.path.relpath(project, tmp_path), "--goal", "Goal"]) == 0
    architect.assert_called_once_with("Goal")
    run.assert_called_once_with(task="Work", commit_message="Commit", allowed_test_files=[])
    plan = TaskManager().load_plan()
    assert plan.project_root == str(project)
    assert plan.status == "completed"


@pytest.mark.parametrize("saved", ["plan", "state"])
def test_cli_does_not_overwrite_recovery_data(project, isolated_workspace, saved):
    if saved == "plan":
        TaskManager().create_plan("Old", str(project), [Task("task-001", "Old work", "Old")])
        saved_file = isolated_workspace / "tasks.json"
    else:
        saved_file = save_interrupted_task(project, isolated_workspace).path
    before = saved_file.read_bytes()
    with patch.object(CodexArchitect, "create_plan") as architect:
        with pytest.raises(SystemExit) as error:
            main.main(["--project", str(project), "--goal", "New"])
    assert error.value.code == 2
    architect.assert_not_called()
    assert saved_file.read_bytes() == before


@pytest.mark.parametrize("provider", ["qwen", "deepseek", "codex"])
def test_provider_cli_compatibility(project, provider):
    arguments = [provider, "Prompt"]
    if provider == "codex":
        arguments.extend(["--project", str(project)])
    with patch(f"main.run_{provider}", return_value="Response") as run:
        assert main.main(arguments) == 0
    if provider == "codex":
        run.assert_called_once_with("Prompt", cwd=str(project))
    else:
        run.assert_called_once_with("Prompt")


@pytest.mark.parametrize("arguments, message", [
    (["--goal", "Goal"], "--project is required"),
    (["codex", "Prompt"], "--project is required"),
    (["--project", ".", "--goal", " "], "--goal cannot be empty"),
    (["qwen", "Prompt", "--goal", "Goal"], "cannot be combined"),
    (["--project", "."], "Provide --project and --goal"),
])
def test_cli_argument_errors(arguments, message, capsys):
    with pytest.raises(SystemExit) as error:
        main.main(arguments)
    assert error.value.code == 2
    assert message in capsys.readouterr().err


def test_cli_from_other_cwd_reports_missing_project(tmp_path):
    result = subprocess.run(
        [sys.executable, str(BASE_DIR / "main.py"), "--project", "missing", "--goal", "Goal"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert f"Project root does not exist: {tmp_path / 'missing'}" in result.stderr


def test_interrupted_plan_resumes_checkpoint_and_remaining_tasks(project, isolated_workspace, monkeypatch):
    manager = TaskManager()
    manager.create_plan("Goal", str(project), [
        Task("task-001", "First", "First commit", allowed_test_files=["test_feature.py"]),
        Task("task-002", "Second", "Second commit", depends_on=["task-001"]),
    ])

    def interrupt_worker(task):
        (project / "example.txt").write_text("Unfinished change", encoding="utf-8")
        (project / "scratch.txt").write_text("Unfinished file", encoding="utf-8")
        raise KeyboardInterrupt

    with patch.object(Scheduler, "_create_worker") as worker:
        worker.return_value.run.side_effect = interrupt_worker
        with patch.object(Scheduler, "_create_reviewer"):
            with pytest.raises(KeyboardInterrupt):
                PlanRunner(manager).run()

    state_manager = StateManager(str(isolated_workspace / "state.json"))
    interrupted = state_manager.load()
    assert interrupted.status == "interrupted"
    assert interrupted.qwen_failures == 1
    assert interrupted.budget_usage["qwen_calls"] == 1
    assert manager.load_plan().current_task_id == "task-001"
    assert GitManager(str(project)).is_clean()
    assert (project / "example.txt").read_text() == "Initial content\n"
    assert not (project / "scratch.txt").exists()

    executions = []

    def finish(scheduler, state, initial_untracked, allowed_test_files):
        executions.append((state.task, state.qwen_failures, dict(state.budget_usage), allowed_test_files))
        state.status = "completed"
        scheduler.state_manager.save(state)
        return TaskResult(True, "Done", None, None, state.starting_head)

    monkeypatch.setattr(Scheduler, "_run_loop", finish)
    assert main.main(["--project", str(project), "--resume"]) == 0
    assert executions[0] == ("First", 1, interrupted.budget_usage, ["test_feature.py"])
    assert executions[1][0:2] == ("Second", 0)
    plan = manager.load_plan()
    assert plan.status == "completed"
    assert [task.attempts for task in plan.tasks] == [1, 1]


@pytest.mark.parametrize("mismatch", ["project_root", "task", "commit_message", "allowed_test_files", "starting_head", "missing"])
def test_plan_recovery_mismatch_preserves_checkpoint(project, tmp_path, isolated_workspace, mismatch):
    manager = TaskManager()
    manager.create_plan("Goal", str(project), [Task("task-001", "Implement feature", "Feature")])
    manager.start_task("task-001")
    state_manager = save_interrupted_task(project, isolated_workspace)
    state = state_manager.load()
    if mismatch == "missing":
        state_manager.clear()
    else:
        setattr(state, mismatch, {
            "project_root": str(tmp_path), "task": "Different task",
            "commit_message": "Different commit", "allowed_test_files": ["test_extra.py"],
            "starting_head": "0" * 40,
        }[mismatch])
        state_manager.save(state)
    before_plan = manager.path.read_bytes()
    before_state = state_manager.path.read_bytes() if state_manager.exists() else None
    with patch.object(Scheduler, "_run_loop") as loop, patch.object(Scheduler, "_rollback") as rollback:
        with pytest.raises(StateRecoveryError):
            PlanRunner(manager).run()
    loop.assert_not_called()
    rollback.assert_not_called()
    assert manager.path.read_bytes() == before_plan
    assert (state_manager.path.read_bytes() if state_manager.exists() else None) == before_state


def test_pending_task_cannot_overwrite_incomplete_checkpoint(project, isolated_workspace):
    manager = TaskManager()
    manager.create_plan("Goal", str(project), [Task("task-001", "New work", "New commit")])
    state_manager = save_interrupted_task(project, isolated_workspace)
    before_plan = manager.path.read_bytes()
    before_state = state_manager.path.read_bytes()
    with pytest.raises(StateRecoveryError, match="refusing to overwrite"):
        PlanRunner(manager).run()
    assert manager.path.read_bytes() == before_plan
    assert state_manager.path.read_bytes() == before_state


def test_resume_cli_rejects_other_project(project, tmp_path, isolated_workspace, capsys):
    TaskManager().create_plan("Goal", str(project), [Task("task-001", "Work", "Commit")])
    with patch.object(PlanRunner, "run") as run:
        with pytest.raises(SystemExit) as error:
            main.main(["--project", str(tmp_path), "--resume"])
    assert error.value.code == 2
    assert "different project" in capsys.readouterr().err
    run.assert_not_called()


@pytest.mark.parametrize("arguments", [
    ["--resume"], ["--resume", "--project", ".", "--goal", "Goal"],
    ["--resume", "--project", ".", "qwen", "Prompt"],
])
def test_resume_cli_rejects_invalid_combinations(arguments):
    with pytest.raises(SystemExit) as error:
        main.main(arguments)
    assert error.value.code == 2


def test_cli_keyboard_interrupt_exits_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [str(BASE_DIR / "main.py"), "qwen", "Prompt"])
    with patch("providers.qwen.run_qwen", side_effect=KeyboardInterrupt):
        with pytest.raises(SystemExit) as error:
            runpy.run_path(str(BASE_DIR / "main.py"), run_name="__main__")
    assert error.value.code == 130
    output = capsys.readouterr().err
    assert "Execution interrupted" in output
    assert "Traceback" not in output
