"""Durable request records; independent of widgets and printed diagnostics."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4
from cli.projects import log_path


class RequestTrace:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else log_path().parent / 'requests'
        self.path = None
        self.request = None
        self.events = []
        self.outcome = 'idle'

    def begin(self, command, argument, project, model, codex_model):
        identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '-' + uuid4().hex[:8]
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / (identifier + '.jsonl')
        self.request = dict(id=identifier, started=datetime.now(timezone.utc).isoformat(), command=command, argument=argument, project=str(project) if project else None, local_model=model, codex_model=codex_model, backend_log=str(self.path.with_suffix(".log")))
        self.events = []
        self.outcome = 'running'
        self._append({'request': self.request})

    def _append(self, value):
        with self.path.open('a', encoding='utf-8') as file:
            file.write(json.dumps(value, ensure_ascii=False) + '\n')

    def record(self, event):
        payload = asdict(event)
        payload['time'] = datetime.now(timezone.utc).isoformat()
        if event.kind != 'delta':
            self.events.append(payload)
        if event.kind in {'error', 'request_failed'}:
            self.outcome = 'failed'
        elif event.kind == 'cancelled':
            self.outcome = 'cancelled'
        elif event.kind == 'done' and self.outcome == 'running':
            self.outcome = 'completed'
        # Streaming output is displayed live; final operation details suffice
        # for diagnostics without thousands of journal writes per response.
        if event.kind != 'delta':
            self._append({'event': payload, 'outcome': self.outcome})

    def latest(self):
        if self.request:
            return self
        try:
            path = max(self.directory.glob('*.jsonl'), key=lambda p: p.name)
            lines = path.read_text(encoding='utf-8').splitlines()
        except (OSError, ValueError):
            return self
        self.path = path
        for line in lines:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if 'request' in value:
                self.request = value['request']
            elif 'event' in value:
                self.events.append(value['event'])
                self.outcome = value.get('outcome', 'unknown')
        if self.outcome == 'running':
            self.outcome = 'incomplete'
        return self

    def render(self):
        self.latest()
        if not self.request:
            return 'No hay peticiones registradas.'
        r = self.request
        lines = [f"Request: {r['id']} · {self.outcome}", f"Project: {r['project']}", f"Command: {r['command']} · Local: {r['local_model']} · Codex: {r['codex_model'] or 'config CLI'}", f"Registro: {self.path}", f"Log técnico: {r['backend_log']}", '\nPETICIÓN ORIGINAL', str(r['argument']), '\nEVENTOS']
        for event in self.events:
            if event['kind'] != 'delta':
                lines.append(f"{event['time']} [{event['kind']}] {event['text']}\n{event['detail']}")
        return '\n'.join(lines)

    def history(self):
        try:
            paths = sorted(self.directory.glob('*.jsonl'), reverse=True)[:20]
        except OSError:
            return []
        records = []
        for path in paths:
            item = RequestTrace(self.directory)
            item.load(path.stem)
            if item.request:
                records.append(item)
        return records

    def load(self, identifier):
        if not identifier or any(c not in '0123456789Tabcdef-' for c in identifier):
            raise ValueError('ID de petición inválido.')
        path = self.directory / (identifier + '.jsonl')
        if not path.is_file():
            raise ValueError('No existe esa petición.')
        self.request = None
        self.events = []
        self.outcome = 'running'
        self.path = path
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if 'request' in value:
                self.request = value['request']
            elif 'event' in value:
                self.events.append(value['event'])
                self.outcome = value.get('outcome', 'unknown')
        if self.outcome == 'running':
            self.outcome = 'incomplete'
        return self
