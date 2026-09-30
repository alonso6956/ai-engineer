import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from cli import workflows
from cli.project import ProjectCreationError, create_project
from cli.raphael import RaphaelCLI
from tools.git import GitManager, GitResult


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch):
    # Tests must not inherit an identity or signing policy from the user's config.
    for key in tuple(os.environ):
        if key.startswith("GIT_") or key == "EMAIL":
            monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def local_identity(monkeypatch):
    initialize = GitManager.initialize

    def initialize_with_test_identity(project_root):
        git = initialize(project_root)
        for name, value in (("user.name", "Project Test"), ("user.email", "project@example.invalid"), ("commit.gpgsign", "false")):
            result = git._run(["config", "--local", name, value])
            assert result.success, str(result)
        return git

    monkeypatch.setattr(GitManager, "initialize", staticmethod(initialize_with_test_identity))


def test_create_project_with_initial_commit(tmp_path, local_identity):
    messages = []
    project = create_project("game-editor", tmp_path, progress=messages.append)
    assert project == (tmp_path / "game-editor").resolve()
    assert (project / ".git").is_dir()
    assert (project / "README.md").read_text() == "# game-editor\n"
    assert (project / ".gitignore").is_file()
    git = GitManager(str(project))
    assert git.head().success
    assert git.is_clean()
    assert git._run(["log", "-1", "--format=%s"]).stdout.strip() == "Initial commit"
    assert git._run(["ls-files"]).stdout.splitlines() == [".gitignore", "README.md"]
    assert git._run(["rev-list", "--count", "HEAD"]).stdout.strip() == "1"
    assert messages[-1] == "Created initial commit"


@pytest.mark.parametrize("form", ["default", "relative", "home", "symlink"])
def test_parent_path_normalization(tmp_path, monkeypatch, local_identity, form):
    parent = tmp_path / "parent with spaces"
    parent.mkdir()
    monkeypatch.chdir(parent)
    if form == "default":
        argument = None
    elif form == "relative":
        argument = "."
    elif form == "home":
        argument = "~/" + os.path.relpath(parent, Path.home())
    else:
        link = tmp_path / "parent-link"
        link.symlink_to(parent, target_is_directory=True)
        argument = str(link)
    assert create_project("renderer", argument) == parent.resolve() / "renderer"


@pytest.mark.parametrize("name", ["", " ", ".", "..", "../escape", "a/../b", "/tmp/escape", "a/b", "a\\b", "..\\escape", "C:\\escape", "bad\x00name", "bad\nname"])
def test_invalid_names_do_not_create_anything(tmp_path, name):
    with patch.object(GitManager, "initialize") as initialize:
        with pytest.raises(ValueError, match="Invalid project name"):
            create_project(name, tmp_path)
    initialize.assert_not_called()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["directory", "file", "symlink", "dangling"])
def test_existing_destination_is_never_reused(tmp_path, kind):
    destination = tmp_path / "existing"
    if kind == "directory":
        destination.mkdir()
        (destination / "keep.txt").write_text("keep")
    elif kind == "file":
        destination.write_text("keep")
    else:
        target = tmp_path / "target"
        if kind == "symlink":
            target.mkdir()
            (target / "keep.txt").write_text("keep")
        destination.symlink_to(target, target_is_directory=True)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with patch.object(GitManager, "initialize") as initialize:
        with pytest.raises(FileExistsError, match="already exists"):
            create_project("existing", tmp_path)
    initialize.assert_not_called()
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert not (destination / ".git").exists()


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_invalid_parent_does_not_create_project(tmp_path, kind):
    parent = tmp_path / "parent"
    if kind == "file":
        parent.touch()
    with pytest.raises((FileNotFoundError, NotADirectoryError), match="Parent"):
        create_project("new", parent)
    assert not (parent / "new").exists()


def test_missing_identity_reports_commit_step_and_leaves_directory(tmp_path, monkeypatch):
    initialize = GitManager.initialize

    def initialize_without_identity(project_root):
        git = initialize(project_root)
        # Git can otherwise infer an identity from the OS account on some hosts.
        assert git._run(["config", "--local", "user.useConfigOnly", "true"]).success
        return git

    monkeypatch.setattr(GitManager, "initialize", staticmethod(initialize_without_identity))
    with pytest.raises(ProjectCreationError) as error:
        create_project("no-identity", tmp_path)
    message = str(error.value)
    assert "Create initial commit failed" in message
    assert "user.name" in message and "user.email" in message
    assert "Directory left for inspection" in message
    git = GitManager(str(tmp_path / "no-identity"))
    assert not git.head().success
    assert (git.root / "README.md").exists()
    assert not git._run(["config", "--local", "--get", "user.name"]).success


@pytest.mark.parametrize("operation, step", [
    ("initialize", "Initialize Git repository"),
    ("add_all", "Stage initial files"),
    ("commit", "Create initial commit"),
    ("head", "Validate new repository"),
])
def test_git_failures_identify_step_and_preserve_active_project(tmp_path, local_identity, operation, step):
    existing = create_project("previous", tmp_path)
    shell = RaphaelCLI()
    shell.dispatch(f"/project {existing}")
    before_head = GitManager(str(existing)).head().stdout
    if operation == "initialize":
        failure = patch.object(GitManager, operation, side_effect=OSError("Git unavailable"))
    else:
        failure = patch.object(GitManager, operation, return_value=GitResult(False, 1, "", "Injected failure"))
    with failure:
        with pytest.raises(ProjectCreationError, match=step + " failed"):
            shell.dispatch(f"/new incomplete {tmp_path}")
    assert shell.project_root == existing
    assert (tmp_path / "incomplete").is_dir()
    assert GitManager(str(existing)).head().stdout == before_head


@pytest.mark.parametrize("filename", ["README.md", ".gitignore"])
def test_file_creation_failure_reports_exact_step(tmp_path, local_identity, monkeypatch, filename):
    original_open = Path.open

    def fail_open(path, *args, **kwargs):
        if path.name == filename:
            raise PermissionError("Cannot write file")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_open)
    with pytest.raises(ProjectCreationError, match=f"Create {filename} failed"):
        create_project("incomplete", tmp_path)
    assert (tmp_path / "incomplete" / ".git").is_dir()


def test_interrupt_reports_step_without_selecting_project(tmp_path):
    shell = RaphaelCLI()
    with patch.object(GitManager, "initialize", side_effect=KeyboardInterrupt):
        with pytest.raises(ProjectCreationError, match="Initialize Git repository failed: KeyboardInterrupt"):
            shell.dispatch(f"/new interrupted {tmp_path}")
    assert shell.project_root is None
    assert (tmp_path / "interrupted").exists()


def test_git_environment_override_rejected_before_creation(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "other.git"))
    with pytest.raises(ValueError, match="environment overrides"):
        create_project("new", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_new_command_selects_via_existing_mechanism_and_dispatches_goal(tmp_path, monkeypatch, local_identity, capsys):
    parent = tmp_path / "My Projects"
    parent.mkdir()
    shell = RaphaelCLI()
    with patch.object(shell, "_activate_project", wraps=shell._activate_project) as activate:
        shell.dispatch(f'/new game-editor "{parent}"')
        activate.assert_called_once_with(parent / "game-editor")
    assert shell.project_root == parent / "game-editor"
    with patch.object(workflows, "run_goal", return_value=SimpleNamespace(status="completed")) as goal:
        shell.dispatch("Implement editor")
    goal.assert_called_once_with(parent / "game-editor", "Implement editor")
    shell.dispatch("/project")
    output = capsys.readouterr().out
    assert "Initialized Git repository" in output
    assert "Created initial commit" in output
    assert "Status       Ready" in output


@pytest.mark.parametrize("command", ["/new", "/new foo a b", '/new foo "unterminated'])
def test_new_invalid_argument_count_and_quotes_show_usage(tmp_path, monkeypatch, command, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", Mock(side_effect=[command, "/help", "/exit"]))
    assert RaphaelCLI().run() == 0
    output = capsys.readouterr().out
    assert "Usage: /new <project-name> [parent-path]" in output
    assert "/new <name> [path]" in output
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", [r"foo\bar", r"..\escape", r"C:\escape"])
def test_new_parser_does_not_sanitize_backslashes(tmp_path, monkeypatch, name):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="Invalid project name"):
        RaphaelCLI().dispatch(f"/new {name}")
    assert list(tmp_path.iterdir()) == []
