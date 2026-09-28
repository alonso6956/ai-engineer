from dataclasses import dataclass
from enum import Enum


class Provider(str, Enum):
    QWEN = "qwen"
    DEEPSEEK = "deepseek"
    CODEX = "codex"


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
    ):
        self.qwen_max_failures = (
            qwen_max_failures
        )

        self.deepseek_max_failures = (
            deepseek_max_failures
        )

    def route(
        self,
        task: str,
        qwen_failures: int = 0,
        deepseek_failures: int = 0,
        force_provider: Provider | None = None,
    ) -> RouteDecision:
        """
        Decide qué modelo utilizar.

        Estrategia inicial:

        1. Si se fuerza un proveedor, usarlo.
        2. Qwen es el worker por defecto.
        3. Si Qwen falla demasiadas veces,
           escalar a DeepSeek.
        4. Si DeepSeek también falla,
           escalar a Codex.
        """

        if force_provider is not None:
            return RouteDecision(
                provider=force_provider,
                reason=(
                    "Provider explicitly forced."
                ),
            )

        if (
            qwen_failures
            < self.qwen_max_failures
        ):
            return RouteDecision(
                provider=Provider.QWEN,
                reason=(
                    "Local Qwen worker is the "
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
                    "Qwen exceeded the allowed "
                    "failure threshold."
                ),
            )

        return RouteDecision(
            provider=Provider.CODEX,
            reason=(
                "Qwen and DeepSeek exceeded "
                "their failure thresholds."
            ),
        )