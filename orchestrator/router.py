from dataclasses import dataclass
from enum import Enum


class Provider(str, Enum):
    LOCAL = "local"
    QWEN = "local"  # Legacy enum alias; persisted "qwen" is accepted below.
    DEEPSEEK = "deepseek"
    CODEX = "codex"

    @classmethod
    def _missing_(cls, value):
        if value == "qwen":
            return cls.LOCAL
        return None


@dataclass
class RouteDecision:
    provider: Provider
    reason: str


class Router:
    """
    Decide qué proveedor debe encargarse de una tarea.

    Esta primera versión usa reglas deterministas.

    El Router NO ejecuta modelos.
    Solo devuelve una decisión.
    """

    def __init__(
        self,
        qwen_max_failures: int = 2,
        deepseek_max_failures: int = 1,
        *,
        local_max_failures: int | None = None,
    ):
        self.local_max_failures = (
            qwen_max_failures if local_max_failures is None else local_max_failures
        )

        self.deepseek_max_failures = (
            deepseek_max_failures
        )

    @property
    def qwen_max_failures(self) -> int:
        """Compatibility with callers using the previous threshold name."""
        return self.local_max_failures

    @qwen_max_failures.setter
    def qwen_max_failures(self, value: int) -> None:
        self.local_max_failures = value

    def route(
        self,
        task: str,
        qwen_failures: int = 0,
        deepseek_failures: int = 0,
        force_provider: Provider | None = None,
        *,
        local_failures: int | None = None,
    ) -> RouteDecision:
        """
        Decide qué modelo utilizar.

        Estrategia inicial:

        1. Si se fuerza un proveedor, usarlo.
        2. El modelo local es el worker por defecto.
        3. Si el modelo local falla demasiadas veces,
           escalar a DeepSeek.
        4. Si DeepSeek también falla,
           escalar a Codex.
        """

        if local_failures is None:
            local_failures = qwen_failures  # Legacy keyword/positional argument.

        if force_provider is not None:
            return RouteDecision(
                provider=force_provider,
                reason=(
                    "Provider explicitly forced."
                ),
            )

        if (
            local_failures
            < self.local_max_failures
        ):
            return RouteDecision(
                provider=Provider.LOCAL,
                reason=(
                    "Local model worker is the "
                    "default low-cost provider."
                ),
            )

        if (
            deepseek_failures
            < self.deepseek_max_failures
        ):
            return RouteDecision(
                provider=Provider.DEEPSEEK,
                reason=(
                    "Local model exceeded the allowed "
                    "failure threshold."
                ),
            )

        return RouteDecision(
            provider=Provider.CODEX,
            reason=(
                "Local model and DeepSeek exceeded "
                "their failure thresholds."
            ),
        )
