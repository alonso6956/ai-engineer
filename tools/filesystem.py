from pathlib import Path


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
        allow_test_writes: bool = False,
    ):
        self.root = Path(project_root).resolve()
        self.allow_test_writes = allow_test_writes

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

    def _check_write_permission(self, path: str) -> None:
        """
        Bloquea modificaciones a archivos que controlan
        o alteran la validación de tests.
        """

        relative = Path(path)

        protected_names = {
            "conftest.py",
            "pytest.ini",
            "tox.ini",
        }

        protected_directories = {
            ".git",
            ".github",
        }

        name = relative.name

        if any(
            part in protected_directories
            for part in relative.parts
        ):
            raise PermissionError(
                f"Directorio protegido: {path}"
            )

        if not self.allow_test_writes:
            if (
                name in protected_names
                or name.startswith("test_")
                or name.endswith("_test.py")
            ):
                raise PermissionError(
                    f"No se puede escribir en archivo de test: {path}"
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