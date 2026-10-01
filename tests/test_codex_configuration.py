import asyncio
import json
import subprocess
from unittest.mock import Mock

import pytest

import config
from cli.projects import RecentProjects
from cli.tui import RaphaelApp
from orchestrator.architect import ArchitectError, CodexArchitect
from orchestrator.events import Event
from providers.codex import codex_failure, model_arguments, run_codex


@pytest.mark.parametrize('model', ['', 'available-model'])
def test_codex_override_shared_by_architect_and_provider(tmp_path, monkeypatch, model):
    monkeypatch.setattr(config, 'CODEX_MODEL', model)
    response = json.dumps({'summary': 'Plan', 'tasks': [{'id': 'task-001', 'description': 'Work', 'commit_message': 'Work', 'depends_on': [], 'acceptance_criteria': ['Works']}]})
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, response, ''))
    monkeypatch.setattr('subprocess.run', runner)
    CodexArchitect(str(tmp_path)).create_plan('Goal')
    run_codex('Goal', str(tmp_path))
    for call in runner.call_args_list:
        command = call.args[0]
        if model:
            assert command[command.index('--model') + 1] == model
        else:
            assert '--model' not in command
        assert '--sandbox' in command


def test_actual_codex_rejection_reason_precedes_echoed_prompt(tmp_path, monkeypatch):
    reason = "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account."
    stderr = 'OpenAI Codex v0.158.0\nuser\nA very long architect prompt\nERROR: ' + json.dumps({'type': 'error', 'status': 400, 'error': {'message': reason}})
    monkeypatch.setattr('subprocess.run', Mock(return_value=subprocess.CompletedProcess([], 1, '', stderr)))
    with pytest.raises(ArchitectError) as caught:
        CodexArchitect(str(tmp_path)).create_plan('Goal')
    first_line = str(caught.value).splitlines()[0]
    assert reason in first_line
    assert '/model codex' in first_line
    assert stderr in str(caught.value)


def test_tui_codex_model_propagation_and_collapsed_error(tmp_path, monkeypatch):
    async def scenario():
        from textual.widgets import Collapsible
        app = RaphaelApp(RecentProjects(tmp_path / 'recent.json'), pick_on_start=False)
        async with app.run_test() as pilot:
            original = app.model
            app.dispatch('/model codex available-model')
            assert app.model == original
            assert app.codex_model == 'available-model'
            factory = Mock()
            monkeypatch.setattr('cli.tui.BackendOperation', factory)
            app.start('goal', 'Goal')
            assert factory.call_args.kwargs['codex_model'] == 'available-model'
            app.operation = None
            app.handle_event(Event('started', 'Codex architect', operation='architect'))
            await pilot.pause()
            app.handle_event(Event('failed', 'Codex architect', 'Model rejected\nLong diagnostic', 'architect'))
            assert app.actions['architect'][0].collapsed
            assert 'Model rejected' in app.actions['architect'][0].title
            app.handle_event(Event('error', 'Model rejected', 'Full traceback'))
            await pilot.pause()
            assert all(item.collapsed for item in app.query(Collapsible))
    asyncio.run(scenario())
