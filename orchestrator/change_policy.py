from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


ALWAYS_PROTECTED_PATHS = {
    "conftest.py",
    "pytest.ini",
    "tox.ini",
    "pyproject.toml",
}

PROTECTED_PREFIXES = (
    ".git/",
    ".github/",
)

SUSPICIOUS_IMPORTS = {
    "pytest",
    "_pytest",
}

SUSPICIOUS_CALLS = {
    "pytest.skip",
    "pytest.xfail",
}

SUSPICIOUS_NAMES = {
    "PYTEST_ADDOPTS",
}


@dataclass
class PolicyResult:
    allowed: bool
    reason: str


class ChangePolicy:
    """
    Deterministic protection against validation bypass.

    This policy runs independently from model reviewers.
    """

    def validate_changed_files(
        self,
        project_root: str,
        changed_files: list[str],
        allowed_test_files: list[str] | None = None,
    ) -> PolicyResult:

        allowed_tests = {
            Path(path).as_posix()
            for path in (
                allowed_test_files or []
            )
        }

        root = Path(project_root).resolve()

        for raw_path in changed_files:
            path = Path(raw_path)

            normalized = path.as_posix()

            if normalized in ALWAYS_PROTECTED_PATHS:
                return PolicyResult(
                    False,
                    f"Protected validation file modified: {normalized}",
                )

            if any(
                normalized.startswith(prefix)
                for prefix in PROTECTED_PREFIXES
            ):
                return PolicyResult(
                    False,
                    f"Protected path modified: {normalized}",
                )

            is_test = self._is_test_file(path)

            if (
                is_test
                and normalized not in allowed_tests
            ):
                return PolicyResult(
                    False,
                    f"Test modification not authorized: {normalized}",
                )

            absolute = (root / path).resolve()

            try:
                absolute.relative_to(root)
            except ValueError:
                return PolicyResult(
                    False,
                    f"Changed file escapes project root: {normalized}",
                )

            if absolute.suffix == ".py" and absolute.exists():
                result = self._inspect_python_file(
                    absolute,
                    is_test_file=is_test,
                    test_authorized=(
                        normalized in allowed_tests
                    ),
                )

                if not result.allowed:
                    return PolicyResult(
                        False,
                        f"{normalized}: {result.reason}",
                    )

        return PolicyResult(
            True,
            "Changed files satisfy deterministic change policy.",
        )

    def _is_test_file(
        self,
        path: Path,
    ) -> bool:

        name = path.name

        return (
            name.startswith("test_")
            or name.endswith("_test.py")
            or "tests" in path.parts
        )

    def _inspect_python_file(
        self,
        path: Path,
        is_test_file: bool = False,
        test_authorized: bool = False,
    ) -> PolicyResult:

        try:
            source = path.read_text(
                encoding="utf-8"
            )

            tree = ast.parse(
                source,
                filename=str(path),
            )

        except (OSError, SyntaxError) as exc:
            return PolicyResult(
                False,
                f"Unable to safely inspect Python file: {exc}",
            )

        for node in ast.walk(tree):

            if isinstance(node, ast.Import):
                for alias in node.names:
                    if (
                        alias.name in SUSPICIOUS_IMPORTS
                        and not (
                            is_test_file
                            and test_authorized
                        )
                    ):
                        return PolicyResult(
                            False,
                            f"Suspicious validation import: {alias.name}",
                        )

            if isinstance(node, ast.ImportFrom):
                module = node.module or ""

                if (
                    (
                        module in SUSPICIOUS_IMPORTS
                        or module.startswith("_pytest.")
                    )
                    and not (
                        is_test_file
                        and test_authorized
                    )
                ):
                    return PolicyResult(
                        False,
                        f"Suspicious validation import: {module}",
                    )

            if isinstance(node, ast.Call):
                name = self._call_name(node.func)

                if name in SUSPICIOUS_CALLS:
                    return PolicyResult(
                        False,
                        f"Suspicious validation call: {name}",
                    )

            if isinstance(node, ast.Name):
                if node.id in SUSPICIOUS_NAMES:
                    return PolicyResult(
                        False,
                        f"Suspicious validation symbol: {node.id}",
                    )

            if isinstance(node, ast.Constant):
                if (
                    isinstance(node.value, str)
                    and "PYTEST_ADDOPTS" in node.value
                ):
                    return PolicyResult(
                        False,
                        "Suspicious PYTEST_ADDOPTS manipulation.",
                    )

        return PolicyResult(
            True,
            "Python file passed validation-bypass inspection.",
        )

    def _call_name(
        self,
        node: ast.AST,
    ) -> str:

        if isinstance(node, ast.Name):
            return node.id

        if isinstance(node, ast.Attribute):
            prefix = self._call_name(node.value)

            if prefix:
                return f"{prefix}.{node.attr}"

            return node.attr

        return ""