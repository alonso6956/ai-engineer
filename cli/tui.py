"""Textual presentation. All engine work lives in cli.backend."""
import asyncio
import time
from functools import partial
from pathlib import Path
import shlex

from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Footer, Header, Input, ListItem, ListView, Static, TextArea

import config
from cli.backend import BackendOperation, HELP
from cli.diagnostics import RequestTrace
from orchestrator.events import Event
from cli.projects import RecentProjects, directories, suggestions, validate_directory


class ProjectPicker(ModalScreen[Path | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancelar", priority=True), Binding("alt+up", "parent", "Padre", priority=True), Binding("ctrl+o", "open", "Abrir carpeta", priority=True)]
    CSS = """
    ProjectPicker { align: center middle; }
    #picker { width: 90%; max-width: 100; height: 90%; border: round $accent; background: $surface; padding: 1 2; }
    #folders { height: 1fr; border: solid $primary; }
    #recents { height: 3; }
    #picker-error { height: auto; color: $error; }
    #picker-buttons { height: 3; }
    """

    def __init__(self, start=None, recents=None, title="Selecciona proyecto"):
        super().__init__()
        self.current = Path(start or Path.cwd())
        self.recents = recents or RecentProjects()
        self.picker_title = title
        self.entries = []
        self.recent_entries = self.recents.load()
        self.generation = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static(self.picker_title, markup=False)
            yield Input(str(self.current), id="path", placeholder="~/Documents/proyecto")
            yield Static("↑ ↓ seleccionar · Enter entrar · Tab completar ruta · Alt+↑ padre · Esc cancelar", markup=False)
            yield ListView(id="folders")
            yield Static("Proyectos recientes", id="recent-label", markup=False)
            yield ListView(*(ListItem(Static(f"{p.name}  {p}", markup=False)) for p in self.recent_entries), id="recents")
            yield Static("", id="picker-error", markup=False)
            with Horizontal(id="picker-buttons"):
                yield Button("Open this folder", id="open-folder", variant="primary")
                yield Button("Padre", id="parent")
                yield Button("Cancelar", id="cancel")

    def on_mount(self):
        if not self.recent_entries:
            self.query_one("#recents").display = False
            self.query_one("#recent-label").display = False
        self.navigate(self.current)

    @work(exclusive=True, group="navigation")
    async def navigate(self, value):
        try:
            current = await asyncio.to_thread(validate_directory, value)
            entries = await asyncio.to_thread(directories, current)
            self.current = current
            self.query_one("#path", Input).value = str(current) + "/"
            await self.show_entries([current.parent, *entries])
            self.query_one("#picker-error", Static).update("")
        except (OSError, ValueError) as error:
            self.query_one("#picker-error", Static).update(str(error))

    async def show_entries(self, entries):
        self.entries = entries
        view = self.query_one("#folders", ListView)
        await view.clear()
        await view.extend(ListItem(Static("../" if p == self.current.parent else p.name + "/", markup=False)) for p in entries)
        view.index = 0 if entries else None

    @on(Input.Changed, "#path")
    def path_changed(self, event):
        self.generation += 1
        self.complete_list(event.value, self.generation)

    @work(exclusive=True, group="suggestions")
    async def complete_list(self, value, generation):
        await asyncio.sleep(.12)
        entries = await asyncio.to_thread(suggestions, value)
        if generation == self.generation:
            if value == str(self.current) + "/":
                entries = [self.current.parent, *entries]
            await self.show_entries(entries)

    async def on_key(self, event):
        if isinstance(self.focused, Input):
            view = self.query_one("#folders", ListView)
            if event.key == "tab" and self.entries:
                event.prevent_default(); event.stop()
                index = view.index or 0
                self.query_one("#path", Input).value = str(self.entries[index]) + "/"
            elif event.key in {"up", "down"}:
                event.prevent_default(); event.stop()
                if self.entries:
                    view.index = max(0, min(len(self.entries)-1, (view.index or 0) + (1 if event.key == "down" else -1)))

    @on(Input.Submitted, "#path")
    def path_submitted(self, event):
        selected = self.query_one("#folders", ListView).index
        target = event.value
        if not Path(target).expanduser().is_dir() and self.entries and selected is not None:
            target = self.entries[selected]
        self.navigate(target)

    @on(ListView.Selected, "#folders")
    def folder_selected(self, event):
        if event.list_view.index is not None:
            self.navigate(self.entries[event.list_view.index])

    @on(ListView.Selected, "#recents")
    def recent_selected(self, event):
        if event.list_view.index is not None:
            self.dismiss(self.recent_entries[event.list_view.index])

    def action_parent(self):
        self.navigate(self.current.parent)

    def action_cancel(self):
        self.dismiss(None)

    def action_open(self):
        self.dismiss(self.current)

    @on(Button.Pressed)
    def button_pressed(self, event):
        {"open-folder": self.action_open, "parent": self.action_parent, "cancel": self.action_cancel}[event.button.id]()


class DiagnosticsScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cerrar", priority=True)]
    CSS = """
    DiagnosticsScreen { align: center middle; }
    #diagnostic-panel { width: 95%; height: 90%; background: $surface; border: round $accent; padding: 1; }
    #diagnostic-scroll { height: 1fr; }
    #diagnostic-text { height: auto; }
    """
    def __init__(self, text, request_id=None):
        super().__init__()
        self.text = text
        self.request_id = request_id

    def compose(self):
        with Vertical(id="diagnostic-panel"):
            yield Static("Observabilidad de petición · Esc cerrar", markup=False)
            with VerticalScroll(id="diagnostic-scroll"):
                yield Static(self.text, id="diagnostic-text", markup=False)
            yield Button("Cerrar", id="close-diagnostics")

    @on(Button.Pressed, "#close-diagnostics")
    def close_panel(self):
        self.dismiss()


class RaphaelApp(App):
    TITLE = "RAPHAEL"
    BINDINGS = [Binding("ctrl+s", "send", "Enviar", priority=True), Binding("ctrl+c", "cancel", "Cancelar", priority=True), Binding("ctrl+q", "leave", "Salir", priority=True), Binding("ctrl+o", "project", "Proyecto", priority=True)]
    CSS = """
    Screen { background: $background; }
    #status { height: auto; max-height: 5; padding: 0 1; background: $surface; color: $text-muted; }
    #conversation { height: 1fr; padding: 1 2; }
    .message { height: auto; margin-bottom: 1; padding: 0 1; border-left: thick $primary; }
    .user { border-left: thick $accent; }
    .error { border-left: thick $error; color: $error; }
    .system { border-left: thick $secondary; color: $text-muted; }
    Collapsible { height: auto; margin-bottom: 1; }
    Collapsible Static { height: auto; max-height: 18; overflow-y: auto; }
    #prompt { height: 8; min-height: 3; max-height: 12; border: round $accent; margin: 0 1; }
    #hints { height: 1; margin: 0 2; color: $text-muted; }
    """

    def __init__(self, recents=None, pick_on_start=True):
        super().__init__()
        self.project_root = None
        self.model = config.LOCAL_MODEL_NAME
        self.codex_model = config.CODEX_MODEL
        self.branch = "—"
        self.state_label = "Ready"
        self.operation = None
        self.actions = {}
        self.stream_widget = None
        self.stream_text = ""
        self.pending_events = []
        self.exit_pending = False
        self.recents = recents or RecentProjects()
        self.trace = RequestTrace(self.recents.path.parent / "requests" if recents is not None else None)
        self.last_goal = None
        self.request_error = False
        self.operation_started = None
        self.pick_on_start = pick_on_start

    def compose(self):
        yield Header()
        yield Static("", id="status", markup=False)
        yield VerticalScroll(id="conversation")
        yield TextArea(id="prompt", soft_wrap=True)
        yield Static("Enter nueva línea · Ctrl+S enviar · Ctrl+O proyecto · Ctrl+P paleta", id="hints", markup=False)
        yield Footer()

    def on_mount(self):
        self.update_status()
        self.set_interval(.05, self.collect_events)
        self.query_one("#prompt", TextArea).focus()
        self.add_message("SYSTEM", "Selecciona un proyecto y escribe un objetivo. /help muestra los comandos.", "system")
        if self.pick_on_start:
            self.action_project()

    def update_status(self):
        elapsed = f" · {int(time.monotonic() - self.operation_started)}s" if self.operation and self.operation_started else ""
        self.query_one("#status", Static).update(f"Project: {self.project_root or '—'} | Branch: {self.branch}\nModel: {self.model} | Codex: {self.codex_model or 'config CLI'} | {self.state_label}{elapsed} | Context: no disponible")

    def add_message(self, role, text, style=""):
        self.query_one("#conversation", VerticalScroll).mount(Static(f"{role}\n{text}", markup=False, classes="message " + style))
        self.query_one("#conversation", VerticalScroll).scroll_end(animate=False)

    def start(self, command, argument=""):
        if self.operation:
            self.add_message("STATUS", "Hay una operación en curso. Ctrl+C para cancelarla.", "system")
            return
        self.actions = {}
        self.stream_widget = None
        self.stream_text = ""
        self.request_error = False
        if command == "goal":
            self.last_goal = (str(self.project_root), argument)
        try:
            self.trace.begin(command, argument, self.project_root, self.model, self.codex_model)
            self.add_message("STATUS", f"Request: {self.trace.request['id']} · /diagnostics para detalles", "system")
            self.operation = BackendOperation(command, argument, self.project_root, self.model, logfile=self.trace.path.with_suffix(".log"), codex_model=self.codex_model)
        except Exception as error:
            self.request_error = True
            self.state_label = "Failed"
            if self.trace.path:
                try:
                    self.trace.record(Event("error", f"Backend startup: {type(error).__name__}: {error}"))
                    self.trace.record(Event("done"))
                except OSError:
                    pass
            self.add_message("ERROR", f"No se pudo iniciar el backend: {type(error).__name__}: {error}", "error")
            self.update_status()
            return
        self.operation_started = time.monotonic()
        self.state_label = "Running"
        self.update_status()

    def action_project(self):
        if isinstance(self.screen, ProjectPicker):
            self.screen.action_open()
            return
        if self.operation:
            self.add_message("STATUS", "Cancela o espera la operación antes de cambiar de proyecto.", "system")
            return
        self.push_screen(ProjectPicker(self.project_root, self.recents), lambda path: self.start("select", str(path)) if path else None)

    def action_send(self):
        editor = self.query_one("#prompt", TextArea)
        text = editor.text.strip()
        if not text:
            return
        if self.operation:
            self.add_message("STATUS", "Espera o cancela la operación; el borrador se conserva.", "system")
            return
        editor.clear()
        self.add_message("YOU", text, "user")
        self.dispatch(text)

    def dispatch(self, text):
        if self.operation and text.split(maxsplit=1)[0] not in {"/quit", "/exit", "/diagnostics", "/requests"}:
            self.add_message("STATUS", "Espera o cancela la operación antes de ejecutar otro comando.", "system")
            return
        if not text.startswith("/"):
            self.start("goal", text)
            return
        parts = text.split(maxsplit=1)
        command = parts[0][1:]
        argument = parts[1] if len(parts) > 1 else ""
        try:
            if argument and command not in {"project", "new", "model", "diagnostics"}:
                raise ValueError(f"/{command} no acepta argumentos.")
            if command == "project":
                if argument:
                    if argument.startswith(('"', "'")) and len(shlex.split(argument)) != 1:
                        raise ValueError("Uso: /project <ruta>")
                    value = shlex.split(argument)[0] if argument.startswith(('"', "'")) else argument
                    self.start("select", value)
                else:
                    self.action_project()
            elif command == "new":
                lexer = shlex.shlex(argument, posix=True); lexer.whitespace_split = True; lexer.commenters = ""; lexer.escape = ""
                values = list(lexer)
                if len(values) not in {1, 2}:
                    raise ValueError("Uso: /new <nombre> [carpeta padre]")
                if len(values) == 2:
                    self.start("new", values)
                else:
                    self.push_screen(ProjectPicker(self.project_root, self.recents, "Selecciona carpeta padre para " + values[0]), lambda path: self.start("new", (values[0], str(path))) if path else None)
            elif command == "model":
                if argument.startswith("codex "):
                    self.codex_model = argument.split(maxsplit=1)[1].strip()
                elif argument == "codex":
                    pass
                elif argument.startswith("local "):
                    self.model = argument.split(maxsplit=1)[1].strip()
                elif argument:
                    self.model = argument
                self.update_status()
                self.add_message("STATUS", f"Modelo local: {self.model}\nCodex: {self.codex_model or 'configuración CLI'}. El ID seleccionado debe estar disponible para tu cuenta.", "system")
            elif command == "context":
                self.add_message("STATUS", "El backend no proporciona uso/límite de contexto. El worker mantiene su contexto por tarea; el historial visible no se reenvía.", "system")
            elif command == "requests":
                records = self.trace.history()
                self.add_message("SYSTEM", "\n".join(f"{r.request['id']} · {r.request['command']} · {r.outcome}" for r in records) + "\n/diagnostics <id> abre una petición guardada.", "system")
            elif command == "diagnostics":
                selected = RequestTrace(self.trace.directory).load(argument) if argument else self.trace
                self.push_screen(DiagnosticsScreen(selected.render(), selected.request["id"] if selected.request else None))
            elif command == "retry":
                saved = self.last_goal
                if saved is None:
                    previous = next((r for r in self.trace.history() if r.request['command'] == 'goal'), None)
                    if previous:
                        saved = (previous.request['project'], previous.request['argument'])
                if saved is None:
                    from orchestrator.task_manager import TaskManager
                    plan = TaskManager().load_plan()
                    if plan:
                        saved = (plan.project_root, plan.goal)
                if saved is None:
                    raise ValueError("No hay un objetivo anterior registrado.")
                if saved[0] != str(self.project_root):
                    raise ValueError(f"La petición pertenece a {saved[0]}. Selecciona ese proyecto primero.")
                self.prefill(saved[1])
                self.add_message("STATUS", "Petición restaurada como borrador. Si hay un plan pendiente, inspecciona /status y usa /resume.", "system")
            elif command == "help":
                self.add_message("SYSTEM", HELP, "system")
            elif command == "clear":
                self.query_one("#conversation", VerticalScroll).remove_children()
            elif command in {"quit", "exit"}:
                self.action_leave()
            elif command in {"status", "plan", "budget", "resume", "git", "tests"}:
                self.start(command)
            else:
                raise ValueError(f"Comando desconocido: /{command}. Usa /help.")
        except (ValueError, OSError) as error:
            self.add_message("ERROR", str(error), "error")

    def collect_events(self):
        if self.operation:
            self.pending_events.extend(self.operation.poll())
            if not self.operation.process.is_alive() and not any(e.kind == "done" for e in self.pending_events):
                from orchestrator.events import Event
                self.pending_events.extend([Event("error", f"Backend terminó inesperadamente ({self.operation.process.exitcode}). Consulta el log y /status."), Event("done")])
        if self.operation:
            self.update_status()
        if self.pending_events:
            batch, self.pending_events = self.pending_events, []
            for event in batch:
                self.handle_event(event)

    def handle_event(self, event):
        if self.trace.path:
            try:
                self.trace.record(event)
            except OSError as error:
                self.add_message("ERROR", f"No se pudo guardar diagnóstico: {error}", "error")
        pane = self.query_one("#conversation", VerticalScroll)
        if event.kind == "ready":
            return
        if event.kind == "project":
            self.project_root = Path(event.text)
            self.branch = event.detail
            try:
                self.recents.add(self.project_root)
            except OSError as error:
                self.add_message("ERROR", f"No se pudieron guardar recientes: {error}", "error")
            self.add_message("STATUS", f"Proyecto abierto: {self.project_root}", "system")
        elif event.kind == "started":
            detail = Static("En curso…", markup=False)
            item = Collapsible(detail, title="● " + event.text, collapsed=True)
            self.actions[event.operation] = (item, detail)
            pane.mount(item)
            self.state_label = event.text
        elif event.kind in {"finished", "failed"}:
            pair = self.actions.get(event.operation)
            if pair:
                item, detail = pair
                item.title = ("✓ " if event.kind == "finished" else "✗ ") + event.text
                detail.update(event.detail or "Completado")
                if event.kind == "failed":
                    item.add_class("error")
                    item.title += " · " + (event.detail.splitlines()[0] if event.detail else "Error")[:400]
                    item.collapsed = True
            if event.text.startswith("Worker"):
                self.stream_widget = None
        elif event.kind == "branch":
            self.branch = event.text
        elif event.kind == "stream_start":
            self.stream_widget = None
            self.stream_text = ""
        elif event.kind == "delta":
            if self.stream_widget is None:
                self.stream_text = ""
                self.stream_widget = Static("", markup=False)
                pane.mount(Collapsible(self.stream_widget, title="Provider · streaming de respuesta JSON", collapsed=True))
            self.stream_text = (self.stream_text + event.text)[-24000:]
            self.stream_widget.update(self.stream_text)
        elif event.kind in {"error", "request_failed"}:
            self.request_error = True
            self.state_label = "Failed"
            self.add_message("ERROR", event.text + "\n/diagnostics muestra etapas y detalles; /retry recupera el prompt; /status y /resume inspeccionan/retoman el plan.", "error")
            pane.mount(Collapsible(Static(event.detail, markup=False), title="✗ " + event.text, collapsed=True, classes="error"))
        elif event.kind in {"message", "status", "cancelled"}:
            self.add_message("RAPHAEL" if event.kind == "message" else "STATUS", event.text, "" if event.kind == "message" else "system")
        elif event.kind == "done":
            if self.operation:
                self.operation.close()
                self.operation = None
            self.state_label = "Failed" if self.request_error else "Ready"
            if self.exit_pending:
                self.exit()
        self.update_status()
        if isinstance(self.screen, DiagnosticsScreen) and event.kind != "delta" and self.trace.request and self.screen.request_id == self.trace.request["id"]:
            self.screen.query_one("#diagnostic-text", Static).update(self.trace.render())
        pane.scroll_end(animate=False)

    def action_cancel(self):
        if self.operation:
            self.operation.cancel()
            self.state_label = "Cancelling…"
            self.update_status()
        else:
            self.notify("No hay operación en curso")

    def action_leave(self):
        if self.operation:
            self.exit_pending = True
            self.action_cancel()
        else:
            self.exit()

    def get_system_commands(self, screen):
        for title, command in [("Change project", "project"), ("New project", "new"), ("Change model", "model"), ("Clear conversation", "clear"), ("Git status", "git"), ("Run tests", "tests"), ("Resume plan", "resume"), ("Request diagnostics", "diagnostics"), ("Restore last goal", "retry"), ("Help", "help"), ("Quit", "quit")]:
            if command in {"new", "model"}:
                callback = partial(self.prefill, "/" + command + " ")
            else:
                callback = partial(self.dispatch, "/" + command)
            yield SystemCommand(title, "/" + command, callback)

    def prefill(self, text):
        editor = self.query_one("#prompt", TextArea)
        editor.load_text(text)
        editor.focus()
