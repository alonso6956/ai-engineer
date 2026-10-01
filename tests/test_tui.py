import asyncio
import signal
import subprocess
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

import config
from cli.backend import BackendOperation, execute
from cli.projects import RecentProjects, directories, suggestions, validate_directory
from cli.tui import ProjectPicker, RaphaelApp
from orchestrator.events import Event, activity, emit, sink, streaming
from providers.local import run_local
from tools.filesystem import FileSystem


@pytest.mark.parametrize('style', ['absolute', 'relative', 'home'])
def test_directory_paths(tmp_path, monkeypatch, style):
    project = tmp_path / 'project'; project.mkdir()
    monkeypatch.chdir(tmp_path); monkeypatch.setenv('HOME', str(tmp_path))
    value = {'absolute': str(project), 'relative': './project', 'home': '~/project'}[style]
    assert validate_directory(value) == project.resolve()


def test_directory_invalid_and_suggestions(tmp_path, monkeypatch):
    with pytest.raises(FileNotFoundError): validate_directory(tmp_path / 'absent')
    file = tmp_path / 'file'; file.write_text('x')
    with pytest.raises(NotADirectoryError): validate_directory(file)
    with pytest.raises(ValueError): validate_directory('')
    (tmp_path / 'Documents').mkdir(); (tmp_path / 'Downloads').mkdir()
    monkeypatch.setenv('HOME', str(tmp_path))
    assert suggestions('~/Doc') == [tmp_path / 'Documents']
    assert len(suggestions('~/Do')) == 2
    assert suggestions(str(tmp_path / 'missing') + '/') == []
    monkeypatch.setattr('cli.projects.os.access', lambda *args: False)
    with pytest.raises(PermissionError): validate_directory(tmp_path)


def test_recents_atomic_order_and_corruption(tmp_path):
    store = RecentProjects(tmp_path / 'config' / 'recent.json')
    for i in range(10): store.add(tmp_path / str(i))
    assert len(store.load()) == 8
    store.add(tmp_path / '5')
    assert store.load()[0] == tmp_path / '5'
    assert len(set(store.load())) == 8
    assert RecentProjects(store.path).load() == store.load()
    store.path.write_text('{bad')
    assert store.load() == []


def test_actual_tools_emit_without_changing_result(tmp_path):
    (tmp_path / 'source.py').write_text('hello')
    events = []; token = sink.set(events.append)
    try:
        fs = FileSystem(str(tmp_path))
        assert fs.read_file('source.py') == 'hello'
        with pytest.raises(FileNotFoundError): fs.read_file('missing')
    finally: sink.reset(token)
    assert [e.kind for e in events] == ['started', 'finished', 'started', 'failed']
    assert events[0].operation == events[1].operation


def test_streaming_preserves_json_and_closes(monkeypatch):
    response = Mock()
    response.iter_lines.return_value = ['data: {"choices":[{"delta":{"content":"{\\\"action\\\":"}}]}', 'data: {"choices":[{"delta":{"content":"\\\"finish\\\"}"}}]}', 'data: [DONE]']
    monkeypatch.setattr('providers.local.requests.post', Mock(return_value=response))
    events = []; token = sink.set(events.append); mode = streaming.set(True)
    try: assert run_local('task') == '{"action":"finish"}'
    finally: sink.reset(token); streaming.reset(mode)
    assert ''.join(e.text for e in events if e.kind == 'delta') == '{"action":"finish"}'
    response.close.assert_called_once()


def test_picker_keyboard_and_large_editor(tmp_path):
    async def scenario():
        (tmp_path / 'child').mkdir()
        app = RaphaelApp(RecentProjects(tmp_path / 'recent.json'), pick_on_start=False)
        async with app.run_test(size=(100, 35)) as pilot:
            from textual.widgets import Input, TextArea, ListView
            editor = app.query_one('#prompt', TextArea)
            editor.load_text('large prompt\n' * 4000)
            assert len(editor.text) > 40000
            await pilot.press('enter')
            assert editor.text.count('\n') >= 4000
            app.push_screen(ProjectPicker(tmp_path, app.recents))
            await pilot.pause(.4)
            picker = app.screen
            picker.query_one('#path', Input).value = str(tmp_path / 'ch')
            await pilot.pause(.4)
            await pilot.press('tab')
            assert picker.query_one('#path', Input).value == str(tmp_path / 'child') + '/'
            await pilot.press('enter'); await pilot.pause(.4)
            assert picker.current == tmp_path / 'child'
            await pilot.press('alt+up'); await pilot.pause(.4)
            assert picker.current == tmp_path
            await pilot.press('escape'); await pilot.pause()
            assert app.screen is not picker
    asyncio.run(scenario())


def test_commands_palette_events_and_draft(tmp_path, monkeypatch):
    async def scenario():
        from textual.widgets import TextArea, Collapsible
        app = RaphaelApp(RecentProjects(tmp_path / 'recent.json'), pick_on_start=False)
        async with app.run_test(size=(100, 35)) as pilot:
            calls = []
            monkeypatch.setattr(app, 'start', lambda *args: calls.append(args))
            for cmd in ('status', 'plan', 'budget', 'resume', 'git', 'tests'):
                app.dispatch('/' + cmd)
            assert [c[0] for c in calls] == ['status', 'plan', 'budget', 'resume', 'git', 'tests']
            app.dispatch('/model test-model'); assert app.model == 'test-model'
            app.dispatch('/project "~/My Projects"'); assert calls[-1] == ('select', '~/My Projects')
            app.dispatch('/new sample /tmp'); assert calls[-1] == ('new', ['sample', '/tmp'])
            app.dispatch('/help'); app.dispatch('/context'); app.dispatch('/invalid')
            app.handle_event(Event('started', 'Read source.py', operation='read'))
            await pilot.pause()
            app.handle_event(Event('finished', 'Read source.py', 'contents', 'read'))
            assert app.actions['read'][0].title == '✓ Read source.py'
            app.handle_event(Event('delta', '{"action":'))
            app.handle_event(Event('delta', '"finish"}'))
            await pilot.pause()
            assert app.stream_text == '{"action":"finish"}'
            app.handle_event(Event('error', 'failure', 'traceback detail'))
            await pilot.pause()
            assert len(app.query(Collapsible)) >= 3
            assert 'Change project' in [c.title for c in app.get_system_commands(app.screen)]
            await pilot.press('ctrl+p'); await pilot.pause(); await pilot.press('escape')
            editor = app.query_one('#prompt', TextArea); editor.load_text('my draft')
            fake = Mock(); app.operation = fake
            app.action_send(); assert editor.text == 'my draft'
            app.action_cancel(); fake.cancel.assert_called_once()
            app.operation = None
            app.dispatch('/clear'); await pilot.pause()
            assert len(app.query_one('#conversation').children) == 0
    asyncio.run(scenario())


def test_backend_real_selection_and_errors(tmp_path):
    subprocess.run(['git', 'init', str(tmp_path / 'project')], check=True, capture_output=True)
    operation = BackendOperation('select', str(tmp_path / 'project'), None, config.LOCAL_MODEL_NAME, tmp_path / 'backend.log')
    events = wait_operation(operation)
    assert any(e.kind == 'project' and e.text == str(tmp_path / 'project') for e in events)
    operation = BackendOperation('goal', 'task', None, config.LOCAL_MODEL_NAME, tmp_path / 'backend.log')
    events = wait_operation(operation)
    assert any(e.kind == 'error' and 'Selecciona' in e.text for e in events)


def wait_operation(operation):
    events = []; deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            events.extend(operation.poll())
            if any(e.kind == 'done' for e in events): break
            time.sleep(.02)
        assert any(e.kind == 'done' for e in events)
        operation.process.join(timeout=3)
        assert not operation.process.is_alive()
        return events
    finally:
        if operation.process.is_alive(): operation.process.kill(); operation.process.join()
        operation.close()


def test_cancel_real_pytest_child_and_session_survives(tmp_path):
    (tmp_path / 'test_slow.py').write_text('import time\ndef test_slow():\n    time.sleep(60)\n')
    operation = BackendOperation('tests', '', tmp_path, config.LOCAL_MODEL_NAME, tmp_path / 'backend.log')
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        events = operation.poll()
        if any(e.kind == 'started' and e.text == 'pytest' for e in events): break
        time.sleep(.02)
    assert operation.ready
    operation.cancel(); operation.cancel()
    events = wait_operation(operation)
    assert any(e.kind == 'cancelled' for e in events)
    assert operation.signal_sent
    # A subsequent backend operation works after cancellation.
    following = BackendOperation('status', '', None, config.LOCAL_MODEL_NAME, tmp_path / 'backend.log')
    assert any(e.kind == 'status' for e in wait_operation(following))


def test_checkpoint_transaction_defers_interrupt_until_saved():
    import os
    from orchestrator.events import checkpoint_transaction
    stages = []
    token = sink.set(lambda event: None)
    try:
        with pytest.raises(KeyboardInterrupt):
            with checkpoint_transaction():
                os.kill(os.getpid(), signal.SIGINT)
                stages.append('commit saved')
                stages.append('checkpoint saved')
        assert stages == ['commit saved', 'checkpoint saved']
    finally:
        sink.reset(token)


def test_picker_fits_standard_terminal(tmp_path):
    async def scenario():
        app = RaphaelApp(RecentProjects(tmp_path / 'recent.json'), pick_on_start=False)
        async with app.run_test(size=(80, 24)) as pilot:
            app.push_screen(ProjectPicker(tmp_path, app.recents))
            await pilot.pause(.4)
            assert app.screen.query_one('#folders').size.height >= 4
            assert app.screen.query_one('#open-folder').region.bottom <= 24
            await pilot.press('escape')
    asyncio.run(scenario())


def test_entrypoint_tty_and_explicit_modes(monkeypatch):
    import main
    monkeypatch.setattr(main.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(main.sys.stdout, 'isatty', lambda: True)
    run = Mock(); monkeypatch.setattr(RaphaelApp, 'run', run)
    assert main.main([]) == 0
    assert main.main(['--tui']) == 0
    assert run.call_count == 2
    classic = Mock(return_value=0)
    monkeypatch.setattr('cli.raphael.RaphaelCLI.run', classic)
    assert main.main(['--classic']) == 0
    classic.assert_called_once()


@pytest.mark.parametrize("open_method", ["keyboard", "mouse"])
def test_first_backend_spawn_from_project_picker_in_fresh_tui(tmp_path, open_method):
    """Fresh interpreter prevents earlier tests from pre-starting resource_tracker."""
    import sys
    from paths import BASE_DIR
    project = tmp_path / 'project'
    subprocess.run(['git', 'init', str(project)], check=True, capture_output=True)
    script = tmp_path / 'picker_integration.py'
    script.write_text('''
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import cli.backend as backend
from cli.projects import RecentProjects
from cli.tui import RaphaelApp, ProjectPicker
from textual.widgets import Input

async def scenario():
    project = Path(sys.argv[2])
    backend.log_path = lambda: project.parent / "backend.log"
    app = RaphaelApp(RecentProjects(project.parent / "recent.json"), pick_on_start=False)
    async with app.run_test(size=(80, 24)) as pilot:
        assert sys.stderr.fileno() == -1
        app.action_project()
        picker = app.screen
        picker.navigate(project)
        await pilot.pause(.4)
        assert picker.current == project
        if sys.argv[3] == "keyboard":
            await pilot.press("ctrl+o")
        else:
            await pilot.click("#open-folder")
        for _ in range(200):
            await pilot.pause(.025)
            if app.operation is None and app.project_root is not None:
                break
        assert app.project_root == project, "Picker failed to activate project"
        assert app.recents.load() == [project]
        assert app.state_label == "Ready"
        app.dispatch("/status")
        for _ in range(200):
            await pilot.pause(.025)
            if app.operation is None:
                break
        assert app.operation is None
        app.action_leave()

if __name__ == "__main__":
    asyncio.run(scenario())
''')
    result = subprocess.run([sys.executable, str(script), str(BASE_DIR), str(project), open_method], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_backend_start_error_is_shown_inside_tui(tmp_path, monkeypatch):
    async def scenario():
        app = RaphaelApp(RecentProjects(tmp_path / 'recent.json'), pick_on_start=False)
        async with app.run_test() as pilot:
            def fail(*args, **kwargs):
                raise ValueError('spawn failed')
            monkeypatch.setattr('cli.tui.BackendOperation', fail)
            app.start('select', str(tmp_path))
            await pilot.pause()
            assert app.operation is None
            assert app.state_label == 'Failed'
            assert app.trace.outcome == 'failed'
            assert 'spawn failed' in str(app.query_one('.error').render())
    asyncio.run(scenario())
