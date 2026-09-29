import os
import subprocess
import sys
from unittest.mock import patch

from orchestrator.change_policy import ChangePolicy
from providers.codex import run_codex
from tools.git import GitManager
from tools.tests import TestRunner as ProjectTestRunner


def test_validation_preserves_tracked_bytecode_and_test_policy(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "calculator.py"
    test = project / "test_calculator.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    test.write_text(
        "from calculator import value\ndef test_value():\n    assert value() == 1\n",
        encoding="utf-8",
    )
    # Reproduce the user's repository: an earlier run committed generated caches.
    environment = os.environ.copy()
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    environment.pop("PYTHONPYCACHEPREFIX", None)
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q"], cwd=project, env=environment,
        check=True, capture_output=True, text=True,
    )
    cache = project / "__pycache__"
    original_bytecode = {path.name: path.read_bytes() for path in cache.glob("*.pyc")}
    assert any(name.startswith("calculator.") for name in original_bytecode)
    assert any(name.startswith("test_calculator.") for name in original_bytecode)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm", "Initial with caches"],
        cwd=project, check=True,
    )
    source.write_text("def value():\n    return 12345\n", encoding="utf-8")
    test.write_text(
        "from calculator import value\ndef test_value():\n    assert value() == 12345\n",
        encoding="utf-8",
    )
    git = GitManager(str(project))
    policy = ChangePolicy()
    # Both the worker and the scheduler perform validation before commit.
    for _ in range(2):
        result = ProjectTestRunner(str(project)).run_pytest()
        assert result.success, str(result)
        assert {path.name: path.read_bytes() for path in cache.glob("*.pyc")} == original_bytecode
        assert git.changed_files() == ["calculator.py", "test_calculator.py"]
        assert policy.validate_changed_files(
            str(project), git.changed_files(), allowed_test_files=["test_calculator.py"],
        ).allowed
        assert not policy.validate_changed_files(str(project), git.changed_files()).allowed


def test_validation_does_not_create_bytecode_in_python_children(tmp_path):
    (tmp_path / "helper.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "child_module.py").write_text("value = 2\n", encoding="utf-8")
    (tmp_path / "test_example.py").write_text(
        "import subprocess, sys\n"
        "import helper\n"
        "def test_child():\n"
        "    assert helper.value == 1\n"
        "    subprocess.run([sys.executable, '-c', 'import child_module'], check=True)\n",
        encoding="utf-8",
    )
    result = ProjectTestRunner(str(tmp_path)).run_pytest()
    assert result.success, str(result)
    assert not list(tmp_path.rglob("*.pyc"))


def test_codex_receives_no_bytecode_environment_without_mutating_parent(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    monkeypatch.setenv("BYTECODE_TEST_SETTING", "preserved")
    with patch("providers.codex.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "Done", "")) as run:
        assert run_codex("Task", cwd=str(tmp_path)) == "Done"
    environment = run.call_args.kwargs["env"]
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert environment["BYTECODE_TEST_SETTING"] == "preserved"
    assert "PYTHONDONTWRITEBYTECODE" not in os.environ
