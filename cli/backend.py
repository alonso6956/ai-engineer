"""Presentation-independent commands and isolated synchronous engine execution."""
import contextlib
import multiprocessing
import os
import signal
import sys
import tempfile
import traceback

import config
from cli import workflows
from cli.project import create_project
from cli.projects import log_path, validate_directory
from orchestrator.events import emit, sink, streaming
from orchestrator.state import StateManager
from orchestrator.task_manager import TaskManager
from tools.git import GitManager
from tools.tests import TestRunner


HELP = """/project [path] — Abrir selector o repositorio
/new <nombre> [padre] — Crear proyecto (sin padre abre selector)
/model [id] — Ver/cambiar modelo local de esta sesión
/model codex <id> — Cambiar modelo de arquitecto/worker/revisor Codex
/status · /plan · /budget · /context · /resume
/git — Git status · /tests — Ejecutar pytest
/diagnostics [id] — Inspeccionar petición y sus eventos
/requests — Listar últimas peticiones registradas
/retry — Recuperar petición como borrador (no ejecuta)
/clear — Limpiar conversación · /help · /quit
Enter: nueva línea · Ctrl+S: enviar · Ctrl+C: cancelar · Ctrl+Q: salir
Ctrl+P: paleta · Ctrl+O: proyecto
Los objetivos usan Codex architect → Local → DeepSeek → Codex.
El historial visible no se envía como conversación al agente."""


def execute(command, argument, project):
    if command == "select":
        root = validate_directory(argument)
        git = GitManager(str(root))
        branch = git.current_branch()
        if not branch.success:
            raise RuntimeError(str(branch))
        emit("project", str(root), branch.stdout.strip() or "detached HEAD")
    elif command == "new":
        name, parent = argument
        root = create_project(name, parent, progress=lambda text: emit("status", text))
        execute("select", str(root), project)
    elif command in {"goal", "resume", "git", "tests"}:
        if project is None:
            raise ValueError("Selecciona un proyecto con /project.")
        if command == "goal":
            result = workflows.run_goal(project, argument)
            emit("request_failed" if result.status == "failed" else "message", f"Plan status: {result.status}")
        elif command == "resume":
            result = workflows.resume_plan(project)
            emit("request_failed" if result.status == "failed" else "message", f"Plan status: {result.status}")
        elif command == "git":
            result = GitManager(project).status()
            emit("status" if result.success else "error", result.stdout or "Working tree limpio", str(result))
        else:
            TestRunner(project).run_pytest()
        branch = GitManager(project).current_branch()
        if branch.success:
            emit("branch", branch.stdout.strip() or "detached HEAD")
    elif command in {"status", "plan", "budget"}:
        plan = TaskManager().load_plan()
        if command == "status":
            state = StateManager().load()
            emit("status", f"Project: {project or '—'}\nPlan: {plan.status if plan else 'Sin plan'}\nSaved project: {plan.project_root if plan else '—'}\nCheckpoint: {state.status if state else '—'}")
        elif command == "plan":
            emit("status", "Sin plan" if plan is None else f"{plan.project_root}\n{plan.goal}\n" + "\n".join(f"{t.status} {t.id}: {t.description}" for t in plan.tasks))
        else:
            from orchestrator.budget import BudgetLimits
            limits = BudgetLimits()
            usage = plan.budget_usage if plan else {}
            emit("status", "\n".join(f"{field}: {usage.get(field, 0)} / {getattr(limits, field)}" for field in ("qwen_calls", "deepseek_calls", "codex_calls")))
    else:
        raise ValueError(f"Comando desconocido: /{command}")


def _entry(connection, command, argument, project, model, logfile, codex_model):
    os.setsid()  # Includes Codex/pytest children in the cancellation signal group.
    interrupted = False

    def interrupt(signum, frame):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            # Ignore subsequent interrupts while the scheduler saves/rolls back.
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupt)
    sink.set(connection.send)
    streaming.set(True)
    config.LOCAL_MODEL_NAME = model
    config.CODEX_MODEL = codex_model
    try:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        with logfile.open("a", encoding="utf-8") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            emit("ready", f"Backend PID: {os.getpid()}")
            try:
                execute(command, argument, project)
            except KeyboardInterrupt:
                emit("cancelled", "Operación cancelada. Usa /status y /resume para inspeccionar/recuperar el plan.")
            except Exception as error:
                traceback.print_exc()
                emit("error", f"{type(error).__name__}: {str(error).splitlines()[0]}", traceback.format_exc())
    except Exception as error:
        emit("error", f"Backend initialization: {type(error).__name__}: {error}", traceback.format_exc())
    finally:
        emit("done")
        connection.close()


class BackendOperation:
    """One operation at a time; never abandon a mutating worker on cancellation."""
    def __init__(self, command, argument, project, model, logfile=None, codex_model=None):
        ctx = multiprocessing.get_context("spawn")
        self.connection, child = ctx.Pipe(duplex=False)
        self.process = ctx.Process(target=_entry, args=(child, command, argument, str(project) if project else None, model, logfile or log_path(), config.CODEX_MODEL if codex_model is None else codex_model))
        self.ready = False
        self.cancel_requested = False
        self.signal_sent = False
        try:
            # Textual's stderr proxy has fileno() == -1. multiprocessing's
            # resource tracker inherits stderr when it first starts, so it
            # needs a real descriptor during this synchronous spawn boundary.
            with contextlib.ExitStack() as stack:
                stderr = sys.__stderr__
                try:
                    if stderr is None or stderr.fileno() < 0:
                        raise ValueError("No real stderr")
                    os.fstat(stderr.fileno())
                except (AttributeError, OSError, ValueError):
                    stderr = stack.enter_context(tempfile.TemporaryFile(mode="w+", encoding="utf-8"))
                with contextlib.redirect_stderr(stderr):
                    self.process.start()
        except BaseException:
            self.connection.close()
            raise
        finally:
            child.close()

    def poll(self):
        events = []
        while self.connection.poll():
            try:
                event = self.connection.recv()
            except EOFError:
                break
            if event.kind == "ready":
                self.ready = True
            events.append(event)
        if self.cancel_requested and self.ready and not self.signal_sent and self.process.is_alive():
            try:
                os.killpg(self.process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            self.signal_sent = True
        return events

    def cancel(self):
        self.cancel_requested = True

    def close(self):
        self.process.join(timeout=0)
        self.connection.close()
