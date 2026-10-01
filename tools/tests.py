from orchestrator.events import activity
import os
import subprocess
import sys
from dataclasses import dataclass
from paths import normalize_path


@dataclass
class TestResult:
    success: bool
    return_code: int
    stdout: str
    stderr: str

    def __str__(self) -> str:
        status = "PASS" if self.success else "FAIL"

        output = [
            f"Status: {status}",
            f"Return code: {self.return_code}",
        ]

        if self.stdout.strip():
            output.append(f"\nSTDOUT:\n{self.stdout.strip()}")

        if self.stderr.strip():
            output.append(f"\nSTDERR:\n{self.stderr.strip()}")

        return "\n".join(output)


class TestRunner:
    """
    Ejecuta tests dentro de un proyecto específico.

    Por ahora solamente soporta pytest.
    """

    def __init__(
        self,
        project_root: str,
        timeout: int = 120,
    ):
        self.root = normalize_path(project_root)
        self.timeout = timeout

        if not self.root.exists():
            raise FileNotFoundError(
                f"El proyecto no existe: {self.root}"
            )

        if not self.root.is_dir():
            raise NotADirectoryError(
                f"La ruta no es un directorio: {self.root}"
            )

    @activity('pytest')
    def run_pytest(
        self,
        target: str | None = None,
    ) -> TestResult:
        """
        Ejecuta pytest dentro de project_root.

        target permite ejecutar algo específico, por ejemplo:

            test_calculator.py

        Si target es None, ejecuta toda la suite.
        """

        command = [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-q",
        ]

        if target:
            target_path = (self.root / target).resolve()

            try:
                target_path.relative_to(self.root)
            except ValueError:
                raise PermissionError(
                    f"Target fuera del proyecto bloqueado: {target}"
                )

            command.append(str(target_path))

        try:
            # Validation must not modify tracked bytecode or create new .pyc files.
            # The environment also applies to Python children started by tests.
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            result = subprocess.run(
                command,
                cwd=self.root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )

            return TestResult(
                success=result.returncode == 0,
                return_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )

        except subprocess.TimeoutExpired as exc:
            return TestResult(
                success=False,
                return_code=-1,
                stdout=exc.stdout or "",
                stderr=(
                    f"Los tests excedieron el timeout "
                    f"de {self.timeout} segundos."
                ),
            )
