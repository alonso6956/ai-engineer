from pathlib import Path

from paths import normalize_path


class FileSystem:
    """
    Acceso controlado al filesystem de un proyecto.

    Todas las operaciones quedan restringidas a project_root.
    """

    IGNORED = {
        ".git",
        ".venv",
        ".pytest_cache",
        "__pycache__",
        "node_modules",
    }

    def __init__(
        self,
        project_root: str,
        allowed_test_files: list[str] | None = None,
    ):
        self.root = normalize_path(project_root)
        self.allowed_test_files = {
            Path(path).as_posix()
            for path in (allowed_test_files or [])
        }

        if not self.root.exists():
            raise FileNotFoundError(
                f"El proyecto no existe: {self.root}"
            )

        if not self.root.is_dir():
            raise NotADirectoryError(
                f"La ruta no es un directorio: {self.root}"
            )

    def _resolve(self, path: str) -> Path:
        """
        Convierte una ruta relativa al proyecto en una ruta absoluta
        y bloquea cualquier intento de salir de project_root.
        """

        target = (self.root / path).resolve()

        try:
            target.relative_to(self.root)
        except ValueError:
            raise PermissionError(
                f"Acceso fuera del proyecto bloqueado: {path}"
            )

        return target

    def _is_test_file(
        self,
        relative_path: Path,
    ) -> bool:
        name = relative_path.name

        return (
            name.startswith("test_")
            or name.endswith("_test.py")
            or "tests" in relative_path.parts
        )

    def _is_always_protected(
        self,
        relative_path: Path,
    ) -> bool:
        normalized = relative_path.as_posix()

        protected_files = {
            "conftest.py",
            "pytest.ini",
            "tox.ini",
        }

        protected_prefixes = (
            ".git/",
            ".github/",
        )

        return (
            normalized in protected_files
            or any(
                normalized.startswith(prefix)
                for prefix in protected_prefixes
            )
        )

    def _check_write_permission(self, path: str) -> None:
        """
        Bloquea modificaciones a archivos de infraestructura
        y a tests no autorizados.
        """

        target = self._resolve(path)
        relative = target.relative_to(self.root)
        normalized = relative.as_posix()

        if self._is_always_protected(relative):
            raise PermissionError(
                f"Protected file cannot be modified: {normalized}"
            )

        if (
            self._is_test_file(relative)
            and normalized not in self.allowed_test_files
        ):
            raise PermissionError(
                f"Test file modification not authorized: {normalized}"
            )

    def read_file(self, path: str) -> str:
        """
        Lee un archivo de texto dentro del proyecto.
        """

        target = self._resolve(path)

        if not target.exists():
            raise FileNotFoundError(
                f"Archivo no encontrado: {path}"
            )

        if not target.is_file():
            raise IsADirectoryError(
                f"No es un archivo: {path}"
            )

        return target.read_text(
            encoding="utf-8",
            errors="replace",
        )

    def write_file(
        self,
        path: str,
        content: str,
    ) -> None:
        """
        Escribe o reemplaza un archivo dentro del proyecto.
        """

        self._check_write_permission(path)

        target = self._resolve(path)

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        target.write_text(
            content,
            encoding="utf-8",
        )

    def list_files(
        self,
        path: str = ".",
    ) -> list[str]:
        """
        Lista los archivos y directorios contenidos directamente
        en una ruta.

        No entra recursivamente en subdirectorios.
        """

        target = self._resolve(path)

        if not target.exists():
            raise FileNotFoundError(
                f"Ruta no encontrada: {path}"
            )

        if not target.is_dir():
            raise NotADirectoryError(
                f"No es un directorio: {path}"
            )

        results = []

        for item in sorted(target.iterdir()):

            if item.name in self.IGNORED:
                continue

            relative = item.relative_to(self.root)

            if item.is_dir():
                results.append(f"{relative}/")
            else:
                results.append(str(relative))

        return results

    def tree(
        self,
        path: str = ".",
        max_depth: int = 3,
    ) -> list[str]:
        """
        Genera una lista recursiva de archivos y directorios.

        max_depth limita cuántos niveles puede explorar.
        """

        start = self._resolve(path)

        if not start.exists():
            raise FileNotFoundError(
                f"Ruta no encontrada: {path}"
            )

        if not start.is_dir():
            raise NotADirectoryError(
                f"No es un directorio: {path}"
            )

        results = []

        def walk(
            directory: Path,
            depth: int,
        ):
            if depth > max_depth:
                return

            try:
                items = sorted(directory.iterdir())
            except PermissionError:
                return

            for item in items:

                if item.name in self.IGNORED:
                    continue

                relative = item.relative_to(self.root)

                if item.is_dir():
                    results.append(f"{relative}/")
                    walk(
                        item,
                        depth + 1,
                    )
                else:
                    results.append(str(relative))

        walk(start, 0)

        return results

    def file_exists(
        self,
        path: str,
    ) -> bool:
        """
        Comprueba si una ruta existe dentro del proyecto.
        """

        return self._resolve(path).exists()

    def delete_file(self, path: str) -> None:
        """
        Elimina un archivo dentro del proyecto.

        No permite eliminar directorios ni acceder fuera
        de project_root.
        """

        target = self._resolve(path)

        if not target.exists():
            return

        if not target.is_file():
            raise IsADirectoryError(
                f"No es un archivo: {path}"
            )

        target.unlink()
