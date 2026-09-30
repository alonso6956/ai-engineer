import json
import runpy
import subprocess
from dataclasses import asdict
from unittest.mock import Mock, patch

import pytest
import requests

import config
import main
from cli import display
from orchestrator.budget import BudgetExceededError, BudgetLimits, BudgetManager, BudgetUsage
from orchestrator.codex_reviewer import CodexReviewer
from orchestrator.codex_worker import CodexWorker
from orchestrator.local_worker import LocalWorker
from orchestrator.plan_runner import PlanRunner
from orchestrator.reviewer import DeepSeekReviewer
from orchestrator.router import Provider, Router
from orchestrator.scheduler import Scheduler, TaskResult
from orchestrator.state import PersistentAttempt, StateManager, TaskState
from orchestrator.task_manager import Task, TaskManager
from paths import BASE_DIR
from providers import local, qwen
from providers.deepseek import run_deepseek
from tools.git import GitManager


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    for module in ("orchestrator.state", "orchestrator.scheduler", "orchestrator.task_manager"):
        monkeypatch.setattr(f"{module}.WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(requests, "post", Mock(side_effect=AssertionError("Unexpected HTTP request")))


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "README.md").write_text("# Test\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm", "Initial"],
        cwd=root, check=True,
    )
    return root


def legacy_state(project, provider="qwen", status="interrupted"):
    return TaskState(
        version=1, status=status, task="Work", project_root=str(project),
        commit_message="Implement", starting_head=GitManager(str(project)).head().stdout.strip(),
        current_provider=provider, qwen_failures=1, deepseek_failures=0, codex_failures=0,
        attempts=[PersistentAttempt("qwen", False, "worker interrupted")],
        last_candidate=None, last_review=None,
        acceptance_criteria=["Works"], allowed_test_files=["test_feature.py"],
        budget_usage={"qwen_calls": 5, "deepseek_calls": 1, "codex_calls": 0},
    )


@pytest.mark.parametrize("local_failures, deepseek_failures, expected", [
    (0, 0, Provider.LOCAL), (1, 0, Provider.LOCAL),
    (2, 0, Provider.DEEPSEEK), (2, 1, Provider.CODEX),
])
def test_router_preserves_escalation_thresholds(local_failures, deepseek_failures, expected):
    decision = Router().route("Task", local_failures=local_failures, deepseek_failures=deepseek_failures)
    assert decision.provider is expected
    assert "Qwen" not in decision.reason


def test_router_legacy_aliases_and_custom_thresholds():
    assert Provider("qwen") is Provider.LOCAL
    assert Provider.QWEN is Provider.LOCAL
    assert Provider.LOCAL.value == "local"
    assert list(Provider) == [Provider.LOCAL, Provider.DEEPSEEK, Provider.CODEX]
    with pytest.raises(ValueError):
        Provider("unknown")
    for router in (Router(qwen_max_failures=4), Router(local_max_failures=4)):
        assert router.route("Task", qwen_failures=3).provider is Provider.LOCAL
        assert router.route("Task", local_failures=4).provider is Provider.DEEPSEEK
        assert router.route("Task", force_provider=Provider.CODEX).provider is Provider.CODEX
    assert Router().route("Task", 2, 1).provider is Provider.CODEX


def test_scheduler_worker_and_reviewer_chain(project):
    scheduler = Scheduler(str(project), local_max_failures=4)
    assert scheduler.router.local_max_failures == 4
    assert Scheduler(str(project), qwen_max_failures=3).router.local_max_failures == 3
    worker = scheduler._create_worker(Provider.LOCAL, ["test_feature.py"], ["Works"])
    assert isinstance(worker, LocalWorker)
    assert worker.model_runner is local.run_local
    assert worker.allowed_test_files == ["test_feature.py"]
    assert worker.acceptance_criteria == ["Works"]
    assert LocalWorker(str(project)).model_runner is local.run_local
    assert scheduler._create_worker(Provider.DEEPSEEK).model_runner is run_deepseek
    assert isinstance(scheduler._create_worker(Provider.CODEX), CodexWorker)
    assert isinstance(scheduler._create_reviewer(Provider.LOCAL), DeepSeekReviewer)
    assert isinstance(scheduler._create_reviewer(Provider.DEEPSEEK), CodexReviewer)


def test_config_defaults_and_environment_overrides(monkeypatch):
    fields = ("LOCAL_MODEL_NAME", "LOCAL_MODEL_BASE_URL", "LOCAL_MODEL_LABEL")
    for field in fields:
        monkeypatch.delenv(field, raising=False)
    with patch("dotenv.load_dotenv"):
        defaults = runpy.run_path(str(BASE_DIR / "config.py"))
    assert defaults["LOCAL_MODEL_NAME"] == "Ternary-Bonsai-2-27B-PQ2_0.gguf"
    assert defaults["LOCAL_MODEL_BASE_URL"] == "http://192.168.3.252:8080/v1"
    for field, value in zip(fields, ("served-alias", "http://localhost:9000/v1", "My Local Model")):
        monkeypatch.setenv(field, value)
    with patch("dotenv.load_dotenv"):
        configured = runpy.run_path(str(BASE_DIR / "config.py"))
    assert configured["LOCAL_MODEL_NAME"] == configured["QWEN_MODEL"] == "served-alias"
    assert configured["LOCAL_MODEL_BASE_URL"] == configured["QWEN_URL"] == "http://localhost:9000/v1"
    assert configured["LOCAL_MODEL_LABEL"] == "My Local Model"


def test_local_provider_uses_configured_api_id_and_existing_protocol(monkeypatch):
    monkeypatch.setattr(config, "LOCAL_MODEL_NAME", "server-model-id")
    monkeypatch.setattr(config, "LOCAL_MODEL_BASE_URL", "http://localhost:9876/v1/")
    response = Mock()
    response.json.return_value = {"choices": [{"message": {"content": "Response"}}]}
    with patch.object(local.requests, "post", return_value=response) as post:
        assert local.run_local("Implement feature") == "Response"
    post.assert_called_once_with(
        "http://localhost:9876/v1/chat/completions",
        json={
            "model": "server-model-id",
            "messages": [
                {"role": "system", "content": "You are a software engineering agent. Follow the user's instructions precisely."},
                {"role": "user", "content": "Implement feature"},
            ],
            "temperature": 0.2,
        },
        timeout=3600,
    )
    response.raise_for_status.assert_called_once()


def test_local_provider_keeps_http_errors():
    response = Mock()
    response.raise_for_status.side_effect = requests.HTTPError("Server rejected model")
    with patch.object(local.requests, "post", return_value=response):
        with pytest.raises(requests.HTTPError, match="Server rejected model"):
            local.run_local("Prompt")
    response.json.assert_not_called()
    with patch.object(local.requests, "post", side_effect=requests.Timeout("timeout")):
        with pytest.raises(requests.Timeout):
            local.run_local("Prompt")


@pytest.mark.parametrize("command", ["local", "qwen"])
def test_direct_commands_use_same_configured_request(command, monkeypatch):
    monkeypatch.setattr(config, "LOCAL_MODEL_NAME", "configured-model")
    response = Mock()
    response.json.return_value = {"choices": [{"message": {"content": "Response"}}]}
    with patch.object(local.requests, "post", return_value=response) as post:
        assert main.main([command, "Prompt"]) == 0
    assert post.call_args.kwargs["json"]["model"] == "configured-model"
    with patch.object(qwen, "run_local", return_value="Legacy response") as run:
        assert qwen.run_qwen("Prompt") == "Legacy response"
    run.assert_called_once_with("Prompt")


def test_ui_uses_generic_role_and_configured_model(monkeypatch, capsys):
    monkeypatch.setattr(config, "LOCAL_MODEL_LABEL", "Bonsai")
    monkeypatch.setattr(config, "LOCAL_MODEL_NAME", "configured-model")
    display.header(None)
    display.budget(None, BudgetLimits())
    output = capsys.readouterr().out
    assert "Workers      Bonsai → DeepSeek → Codex" in output
    assert "Local Model  configured-model" in output
    assert "Local        0 / unlimited" in output
    assert "Qwen" not in output


def test_budgets_preserve_limits_and_legacy_serialization():
    budget = BudgetManager(usage=BudgetUsage(qwen_calls=4, deepseek_calls=1))
    assert budget.used(Provider.LOCAL) == budget.usage.local_calls == 4
    assert budget.limit(Provider.LOCAL) is None
    for _ in range(20):
        budget.consume(Provider.LOCAL)
    assert asdict(budget.usage) == {"qwen_calls": 24, "deepseek_calls": 1, "codex_calls": 0}
    for _ in range(2):
        budget.consume(Provider.DEEPSEEK)
    with pytest.raises(BudgetExceededError):
        budget.consume(Provider.DEEPSEEK)
    budget.consume(Provider.CODEX)
    with pytest.raises(BudgetExceededError):
        budget.consume(Provider.CODEX)
    limits = BudgetLimits(qwen_calls=2)
    assert limits.local_calls == 2
    limits.local_calls = 1
    limited = BudgetManager(limits=limits)
    limited.consume(Provider.QWEN)
    with pytest.raises(BudgetExceededError):
        limited.consume(Provider.LOCAL)


def test_legacy_state_round_trip_preserves_counters(project):
    manager = StateManager()
    manager.path.parent.mkdir()
    state = legacy_state(project)
    manager.path.write_text(json.dumps(asdict(state)))
    loaded = manager.load()
    assert loaded.local_failures == loaded.qwen_failures == 1
    assert loaded.budget_usage["qwen_calls"] == 5
    loaded.local_failures += 1
    manager.save(loaded)
    payload = json.loads(manager.path.read_text())
    assert payload["qwen_failures"] == 2
    assert "local_failures" not in payload
    assert payload["budget_usage"] == state.budget_usage
    assert payload["attempts"] == asdict(state)["attempts"]


@pytest.mark.parametrize("current_provider, attempt_provider, status, expected", [
    ("qwen", "qwen", "interrupted", 1),
    ("local", "qwen", "interrupted", 1),
    ("qwen", "local", "interrupted", 1),
    ("local", "local", "interrupted", 1),
    ("qwen", "qwen", "running", 2),
])
def test_legacy_resume_does_not_duplicate_failure_counts(project, current_provider, attempt_provider, status, expected):
    state = legacy_state(project, provider=current_provider, status=status)
    state.attempts[0].provider = attempt_provider
    manager = StateManager()
    manager.save(state)
    scheduler = Scheduler(str(project))
    with patch.object(scheduler, "_run_loop", return_value="resumed") as loop:
        assert scheduler.resume_task() == "resumed"
    resumed = loop.call_args.kwargs["state"]
    assert resumed.local_failures == expected
    assert len(resumed.attempts) == expected
    assert resumed.budget_usage == state.budget_usage
    assert all(attempt.provider is Provider.LOCAL for attempt in scheduler._attempt_results_from_state(resumed))
    assert loop.call_args.kwargs["acceptance_criteria"] == state.acceptance_criteria
    assert loop.call_args.kwargs["allowed_test_files"] == state.allowed_test_files


def test_plan_budget_survives_legacy_resume_and_next_task(project, monkeypatch):
    manager = TaskManager()
    manager.create_plan("Goal", str(project), [
        Task("task-001", "Work", "Implement", acceptance_criteria=["Works"], allowed_test_files=["test_feature.py"]),
        Task("task-002", "Next", "Next commit", depends_on=["task-001"]),
    ])
    manager.update_budget_usage({"qwen_calls": 4, "deepseek_calls": 1, "codex_calls": 0})
    manager.start_task("task-001")
    StateManager().save(legacy_state(project))
    counts = []

    def finish(scheduler, state, initial_untracked, acceptance_criteria, allowed_test_files):
        budget = scheduler._create_budget(state)
        counts.append(budget.used(Provider.LOCAL))
        budget.consume(Provider.LOCAL)
        scheduler._save_budget(state, budget)
        state.status = "completed"
        state.commit_hash = state.starting_head
        scheduler.state_manager.save(state)
        return TaskResult(True, "Done", None, None, state.commit_hash)

    monkeypatch.setattr(Scheduler, "_run_loop", finish)
    plan = PlanRunner(manager).run()
    assert plan.status == "completed"
    assert counts == [5, 6]
    assert plan.budget_usage == {"qwen_calls": 7, "deepseek_calls": 1, "codex_calls": 0}
    assert TaskManager().load_plan().budget_usage == plan.budget_usage
