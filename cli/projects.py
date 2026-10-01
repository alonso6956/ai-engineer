"""Filesystem navigation and minimal recent-project persistence."""
import json
import os
from pathlib import Path
from platformdirs import user_config_path, user_log_path
from paths import normalize_path


def validate_directory(value):
    if not str(value).strip():
        raise ValueError("La ruta no puede estar vacía.")
    path = normalize_path(value)
    if not path.exists():
        raise FileNotFoundError(f"No existe: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"No es un directorio: {path}")
    if not os.access(path, os.R_OK | os.X_OK):
        raise PermissionError(f"No se puede acceder a: {path}")
    return path


def directories(path):
    return sorted((p for p in validate_directory(path).iterdir() if p.is_dir()), key=lambda p: p.name.casefold())


def suggestions(value):
    if not value:
        return []
    path = Path(value).expanduser()
    parent = path if value.endswith("/") else path.parent
    prefix = "" if value.endswith("/") else path.name.casefold()
    try:
        return [p for p in directories(parent) if p.name.casefold().startswith(prefix)]
    except OSError:
        return []


class RecentProjects:
    def __init__(self, path=None):
        self.path = Path(path) if path else user_config_path("raphael") / "recent-projects.json"

    def load(self):
        try:
            data = json.loads(self.path.read_text())
            if not isinstance(data, list):
                return []
            return [Path(p) for p in data if isinstance(p, str) and Path(p).is_absolute()][:8]
        except (OSError, ValueError):
            return []

    def add(self, path):
        path = normalize_path(path)
        values = [path, *(p for p in self.load() if p != path)][:8]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps([str(p) for p in values]), encoding="utf-8")
        temporary.replace(self.path)


def log_path():
    return user_log_path("raphael") / "backend.log"
