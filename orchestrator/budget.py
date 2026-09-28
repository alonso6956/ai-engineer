from dataclasses import dataclass

from orchestrator.router import Provider


class BudgetExceededError(RuntimeError):
	pass


@dataclass
class BudgetLimits:
	qwen_calls: int | None = None
	deepseek_calls: int | None = 3
	codex_calls: int | None = 1


@dataclass
class BudgetUsage:
	qwen_calls: int = 0
	deepseek_calls: int = 0
	codex_calls: int = 0


class BudgetManager:
	def __init__(
		self,
		limits: BudgetLimits | None = None,
		usage: BudgetUsage | None = None,
	):
		self.limits = limits or BudgetLimits()
		self.usage = usage or BudgetUsage()

	def _field(self, provider: Provider) -> str:
		return f"{provider.value}_calls"

	def used(self, provider: Provider) -> int:
		return getattr(
			self.usage,
			self._field(provider),
		)

	def limit(self, provider: Provider) -> int | None:
		return getattr(
			self.limits,
			self._field(provider),
		)

	def can_use(self, provider: Provider) -> bool:
		limit = self.limit(provider)
		return limit is None or self.used(provider) < limit

	def consume(self, provider: Provider) -> None:
		if not self.can_use(provider):
			raise BudgetExceededError(
				f"Budget exhausted for {provider.value}: "
				f"{self.used(provider)}/{self.limit(provider)} "
				"calls used."
			)

		field = self._field(provider)
		setattr(
			self.usage,
			field,
			getattr(self.usage, field) + 1,
		)
