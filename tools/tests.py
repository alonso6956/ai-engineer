import subprocess
from dataclasses import dataclass
from pathlib import Path


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
        self.root = Path(project_root).resolve()
        self.timeout = timeout

        if not self.root.exists():
            raise FileNotFoundError(
                f"El proyecto no existe: {self.root}"
            )

        if not self.root.is_dir():
            raise NotADirectoryError(
                f"La ruta no es un directorio: {self.root}"
            )

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
            "python",
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
            result = subprocess.run(
                command,
                cwd=self.root,
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