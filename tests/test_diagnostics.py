import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock

from cli.diagnostics import RequestTrace
from cli.projects import RecentProjects
from cli.tui import DiagnosticsScreen, RaphaelApp
from orchestrator.events import Event, sink
from orchestrator.plan_runner import PlanRunner
from orchestrator.task_manager import Task, TaskManager


def test_request_journal_recovers_cause_prompt_and_history(tmp_path):
    trace = RequestTrace(tmp_path)
    trace.begin('goal', 'original multiline\nprompt', '/project', 'local', 'codex')
    request_id = trace.request['id']
    trace.record(Event('status', 'Task task-001'))
    trace.record(Event('request_failed', 'Budget exhausted', 'Provider: deepseek'))
    trace.record(Event('done'))
    restored = RequestTrace(tmp_path).load(request_id)
    assert restored.outcome == 'failed'
    assert restored.request['argument'] == 'original multiline\nprompt'
    assert 'Budget exhausted' in restored.render()
    assert 'deepseek' in restored.render()
    assert restored.request['backend_log'].endswith('.log')
    trace.begin('status', '', '/project', 'local', 'codex'); trace.record(Event('done'))
    assert len(trace.history()) == 2
    assert any(r.outcome == 'failed' for r in trace.history())


def test_plan_runner_failure_emits_exact_cause_and_review(tmp_path, monkeypatch):
    @dataclass
    class Attempt:
        provider: str
        success: bool
        reason: str
    manager = TaskManager(str(tmp_path / 'plan.json'))
    manager.create_plan('Goal', str(tmp_path), [Task('task-001', 'Work', 'Commit')])
    scheduler = Mock()
    scheduler.state_manager.get_incomplete_task.return_value = None
    scheduler.state_manager.get_completed_task.return_value = None
    scheduler.state_manager.load.return_value = None
    scheduler.run_task.return_value = SimpleNamespace(success=False, commit_hash=None, message='Budget exhausted for deepseek: 3/3', attempts=[Attempt('local', False, 'HTTP 500')], review=None, candidate=None)
    monkeypatch.setattr('orchestrator.plan_runner.Scheduler', Mock(return_value=scheduler))
    events = []; token = sink.set(events.append)
    try:
        assert PlanRunner(manager).run().status == 'failed'
    finally:
        sink.reset(token)
    failure = next(e for e in events if e.kind == 'request_failed')
    assert 'Budget exhausted' in failure.text
    assert 'HTTP 500' in failure.detail


def test_tui_diagnostics_failed_status_and_retry_survive_queries(tmp_path, monkeypatch):
    async def scenario():
        from textual.widgets import TextArea
        app = RaphaelApp(RecentProjects(tmp_path / 'recent.json'), pick_on_start=False)
        async with app.run_test() as pilot:
            app.project_root = tmp_path
            app.trace.begin('goal', 'My original request', tmp_path, 'local', 'codex')
            request_id = app.trace.request['id']
            app.handle_event(Event('request_failed', 'pytest failed', '2 tests failed'))
            app.handle_event(Event('done'))
            assert app.state_label == 'Failed'
            app.trace.begin('status', '', tmp_path, 'local', 'codex'); app.trace.record(Event('done'))
            app.dispatch('/diagnostics ' + request_id)
            await pilot.pause()
            assert isinstance(app.screen, DiagnosticsScreen)
            assert '2 tests failed' in app.screen.text
            await pilot.press('escape')
            app.dispatch('/retry')
            assert app.query_one('#prompt', TextArea).text == 'My original request'
            assert app.operation is None
    asyncio.run(scenario())


def test_reviewer_rejection_is_failed_activity():
    from orchestrator.events import activity
    events = []; token = sink.set(events.append)
    try:
        @activity('Review')
        def review():
            return SimpleNamespace(approved=False, reason='Unmet criterion')
        assert not review().approved
    finally:
        sink.reset(token)
    assert events[-1].kind == 'failed'
    assert 'Unmet criterion' in events[-1].detail


def test_retry_recovers_existing_plan_goal_without_request_journal(tmp_path, monkeypatch):
    async def scenario():
        from textual.widgets import TextArea
        manager = TaskManager(str(tmp_path / 'plan.json'))
        manager.create_plan('Persisted original goal', str(tmp_path), [Task('task-001', 'Work', 'Commit')])
        monkeypatch.setattr('orchestrator.task_manager.TaskManager', lambda: manager)
        app = RaphaelApp(RecentProjects(tmp_path / 'recent.json'), pick_on_start=False)
        async with app.run_test() as pilot:
            app.project_root = tmp_path
            app.dispatch('/retry')
            assert app.query_one('#prompt', TextArea).text == 'Persisted original goal'
            assert app.operation is None
    asyncio.run(scenario())


def test_backend_log_initialization_failure_is_observable(tmp_path):
    from cli.backend import BackendOperation
    from tests.test_tui import wait_operation
    blocked = tmp_path / 'file'; blocked.write_text('not a directory')
    operation = BackendOperation('status', '', None, 'local', blocked / 'backend.log')
    events = wait_operation(operation)
    assert any(e.kind == 'error' and 'Backend initialization' in e.text for e in events)
