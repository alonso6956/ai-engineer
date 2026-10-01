"""Optional structured telemetry. The engine never imports presentation widgets."""
from contextlib import contextmanager
import signal
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from uuid import uuid4


@dataclass(frozen=True)
class Event:
    kind: str
    text: str = ""
    detail: str = ""
    operation: str = ""


sink = ContextVar("raphael_event_sink", default=None)
streaming = ContextVar("raphael_streaming", default=False)


def emit(kind, text="", detail="", operation=""):
    callback = sink.get()
    if callback:
        callback(Event(kind, str(text), str(detail), operation))


def activity(label):
    """Report real operations, preserving return values and exceptions."""
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            if sink.get() is None:
                return function(*args, **kwargs)
            identifier = uuid4().hex
            title = label
            if len(args) > 1 and isinstance(args[1], (str, list)):
                title += " " + str(args[1])[:120]
            emit("started", title, operation=identifier)
            try:
                result = function(*args, **kwargs)
            except BaseException as error:
                emit("failed", title, str(error) or type(error).__name__, identifier)
                raise
            success = getattr(result, "success", getattr(result, "approved", True))
            emit("finished" if success else "failed", title, str(result)[:24000], identifier)
            return result
        return call
    return decorate


@contextmanager
def checkpoint_transaction():
    """In the isolated TUI engine defer SIGINT through commit/checkpoint writes.

    Classic callers retain their existing signal behavior. On unblocking,
    PlanRunner can reconcile a completed checkpoint if cancellation was pending.
    """
    previous = None
    if sink.get() is not None and hasattr(signal, "pthread_sigmask"):
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
    try:
        yield
    finally:
        if previous is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
