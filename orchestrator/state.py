import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


TASK_STATUSES = frozenset({
	"idle",
	"running",
	"completed",
	"failed",
	"interrupted",
})


class StateRecoveryError(RuntimeError):
	pass


@dataclass
class PersistentAttempt:
	provider: str
	success: bool
	reason: str


@dataclass
class TaskState:
	version: int
	status: str
	task: str
	project_root: str
	commit_message: str
	starting_head: str
	current_provider: str | None
	qwen_failures: int
	deepseek_failures: int
	codex_failures: int
	attempts: list[PersistentAttempt]
	last_candidate: dict[str, Any] | None
	last_review: dict[str, Any] | None
	budget_usage: dict[str, int] = field(default_factory=dict)

	def __post_init__(self) -> None:
		if self.status not in TASK_STATUSES:
			raise ValueError(
				f"Invalid task status: {self.status}"
			)
		for name in (
			"qwen_failures",
			"deepseek_failures",
			"codex_failures",
		):
			if getattr(self, name) < 0:
				raise ValueError(
					f"{name} cannot be negative."
				)


class StateManager:
	def __init__(self, path: str):
		self.path = Path(path)
		self.temporary_path = Path(f"{self.path}.tmp")

	def save(self, state: TaskState) -> None:
		self.path.parent.mkdir(
			parents=True,
			exist_ok=True,
		)

		payload = asdict(state)
		with self.temporary_path.open(
			"w",
			encoding="utf-8",
		) as state_file:
			json.dump(
				payload,
				state_file,
				ensure_ascii=False,
				indent=2,
			)
			state_file.write("\n")
			state_file.flush()
			os.fsync(state_file.fileno())

		os.replace(
			self.temporary_path,
			self.path,
		)

	def load(self) -> TaskState | None:
		if not self.path.exists():
			return None

		with self.path.open(
			"r",
			encoding="utf-8",
		) as state_file:
			payload = json.load(state_file)

		payload["attempts"] = [
			PersistentAttempt(**attempt)
			for attempt in payload["attempts"]
		]
		return TaskState(**payload)

	def has_incomplete_task(self) -> bool:
		state = self.load()
		if state is None:
			return False
		return state.status in {
			"running",
			"interrupted",
		}

	def get_incomplete_task(self) -> TaskState | None:
		state = self.load()
		if state is None:
			return None
		if state.status not in {
			"running",
			"interrupted",
		}:
			return None
		return state

	def exists(self) -> bool:
		return self.path.is_file()

	def clear(self) -> None:
		self.path.unlink(missing_ok=True)
		self.temporary_path.unlink(missing_ok=True)
