from dataclasses import asdict, dataclass, field
from paths import WORKSPACE_DIR, normalize_path

from providers.deepseek import run_deepseek
from providers.local import run_local
from orchestrator.change_policy import ChangePolicy
from orchestrator.budget import (
    BudgetExceededError,
    BudgetLimits,
    BudgetManager,
    BudgetUsage,
)
from orchestrator.codex_worker import CodexWorker
from orchestrator.local_worker import (
    LocalWorker,
    CandidateResult,
)
from orchestrator.codex_reviewer import CodexReviewer
from orchestrator.reviewer import (
    DeepSeekReviewer,
    ReviewResult,
)
from orchestrator.router import (
    Provider,
    Router,
)
from orchestrator.state import (
    PersistentAttempt,
    StateRecoveryError,
    StateManager,
    TaskState,
)
from tools.filesystem import FileSystem
from tools.git import GitManager


@dataclass
class AttemptResult:
    provider: Provider
    success: bool
    reason: str


@dataclass
class TaskResult:
    success: bool
    message: str
    candidate: CandidateResult | None
    review: ReviewResult | None
    commit_hash: str | None
    attempts: list[AttemptResult] = field(
        default_factory=list
    )


class Scheduler:
    """
    Coordina la ejecución completa de una tarea.

    Flujo:

        Task
          ↓
        Local Worker
          ↓
        CandidateResult
          ↓
        DeepSeek Reviewer
          ↓
        APPROVE / REJECT
          ↓
        commit / rollback

    El Scheduler es la única capa responsable de decidir
    si un candidato aprobado termina en un commit.
    """

    def __init__(
        self,
        project_root: str,
        max_worker_steps: int = 20,
        qwen_max_failures: int = 2,
        deepseek_max_failures: int = 1,
        budget_limits: BudgetLimits | None = None,
        initial_budget_usage: dict[str, int] | None = None,
        *,
        local_max_failures: int | None = None,
    ):
        self.project_root = str(normalize_path(project_root))
        self.max_worker_steps = max_worker_steps
        self.initial_budget_usage = dict(
            initial_budget_usage or {}
        )
        self.budget_limits = (
            budget_limits or BudgetLimits()
        )

        self.router = Router(
            local_max_failures=(qwen_max_failures if local_max_failures is None else local_max_failures),
            deepseek_max_failures=deepseek_max_failures,
        )

        self.change_policy = ChangePolicy()

        self.git = GitManager(
            self.project_root
        )
        self.fs = FileSystem(
            self.project_root
        )
        state_path = WORKSPACE_DIR / "state.json"
        self.state_manager = StateManager(
            str(state_path)
        )

    def _record_attempt(
        self,
        state: TaskState,
        provider: Provider,
        success: bool,
        reason: str,
    ) -> None:
        state.attempts.append(
            PersistentAttempt(
                provider=provider.value,
                success=success,
                reason=reason,
            )
        )
        if not success:
            state.current_provider = None
        self.state_manager.save(state)

    def _create_budget(
        self,
        state: TaskState,
    ) -> BudgetManager:
        usage = BudgetUsage(
            qwen_calls=state.budget_usage.get(
                "qwen_calls",
                0,
            ),
            deepseek_calls=state.budget_usage.get(
                "deepseek_calls",
                0,
            ),
            codex_calls=state.budget_usage.get(
                "codex_calls",
                0,
            ),
        )
        return BudgetManager(
            limits=self.budget_limits,
            usage=usage,
        )

    def _save_budget(
        self,
        state: TaskState,
        budget: BudgetManager,
    ) -> None:
        state.budget_usage = {
            "qwen_calls": budget.usage.local_calls,
            "deepseek_calls": budget.usage.deepseek_calls,
            "codex_calls": budget.usage.codex_calls,
        }
        self.state_manager.save(state)

    def _attempt_results_from_state(
        self,
        state: TaskState,
    ) -> list[AttemptResult]:
        return [
            AttemptResult(
                provider=Provider(attempt.provider),
                success=attempt.success,
                reason=attempt.reason,
            )
            for attempt in state.attempts
        ]

    def _create_worker(
        self,
        provider: Provider,
        allowed_test_files: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
    ) -> LocalWorker | CodexWorker:
        if provider == Provider.LOCAL:
            return LocalWorker(
                str(self.project_root),
                max_steps=self.max_worker_steps,
                model_runner=run_local,
                acceptance_criteria=acceptance_criteria,
                allowed_test_files=allowed_test_files,
            )
        if provider == Provider.DEEPSEEK:
            return LocalWorker(
                str(self.project_root),
                max_steps=self.max_worker_steps,
                model_runner=run_deepseek,
                acceptance_criteria=acceptance_criteria,
                allowed_test_files=allowed_test_files,
            )
        if provider == Provider.CODEX:
            return CodexWorker(
                str(self.project_root),
                acceptance_criteria=acceptance_criteria,
                allowed_test_files=allowed_test_files,
            )
        raise ValueError(
            f"Unknown provider: {provider}"
        )

    def _create_reviewer(
        self,
        worker_provider: Provider,
    ):
        """
        Selecciona un reviewer distinto del modelo
        que produjo el candidato.
        Local    -> DeepSeek
        DeepSeek -> Codex
        """
        if worker_provider == Provider.LOCAL:
            return DeepSeekReviewer()
        if worker_provider == Provider.DEEPSEEK:
            return CodexReviewer()
        if worker_provider == Provider.CODEX:
            raise NotImplementedError(
                "Review strategy for Codex worker "
                "is not implemented yet."
            )
        raise ValueError(
            f"Unknown provider: {worker_provider}"
        )

    def _register_failure(
        self,
        provider: Provider,
        local_failures: int,
        deepseek_failures: int,
        codex_failures: int,
    ) -> tuple[int, int, int]:
        if provider == Provider.LOCAL:
            local_failures += 1
        elif provider == Provider.DEEPSEEK:
            deepseek_failures += 1
        elif provider == Provider.CODEX:
            codex_failures += 1
        return (
            local_failures,
            deepseek_failures,
            codex_failures,
        )

    def _validate_change_policy(
        self,
        changed_files: list[str],
        allowed_test_files: list[str] | None = None,
    ) -> tuple[bool, str]:
        result = self.change_policy.validate_changed_files(
            project_root=str(self.project_root),
            changed_files=changed_files,
            allowed_test_files=allowed_test_files,
        )

        return result.allowed, result.reason

    def _rollback(
        self,
        initial_untracked: set[str],
    ) -> None:
        """
        Primero limpia el índice, luego restaura archivos tracked
        y elimina solo los archivos untracked creados durante
        esta tarea.
        """

        reset_result = self.git.reset_index()
        if not reset_result.success:
            raise RuntimeError(
                "Failed to reset Git index:\n"
                f"{reset_result}"
            )

        restore_result = (
            self.git.restore_worktree()
        )

        if not restore_result.success:
            raise RuntimeError(
                "Failed to restore working tree:\n"
                f"{restore_result}"
            )

        current_untracked = set(
            self.git.untracked_files()
        )

        new_untracked = (
            current_untracked
            - initial_untracked
        )

        for path in sorted(new_untracked):
            try:
                self.fs.delete_file(path)
                print(
                    f"Rollback removed: {path}"
                )
            except Exception as error:
                print(
                    f"Rollback could not remove "
                    f"{path}: {error}"
                )

    def _provider_from_state(
        self,
        value: str | None,
    ) -> Provider | None:
        if value is None:
            return None
        try:
            return Provider(value)
        except ValueError as error:
            raise StateRecoveryError(
                "State contains an unknown "
                f"provider: {value}"
            ) from error

    def resume_task(self) -> TaskResult:
        state = self.state_manager.get_incomplete_task()
        if state is None:
            raise StateRecoveryError(
                "No incomplete task is available "
                "for recovery."
            )

        saved_root = normalize_path(state.project_root)
        configured_root = normalize_path(self.project_root)
        if saved_root != configured_root:
            raise StateRecoveryError(
                "Saved task belongs to a different project.\n"
                f"Saved: {saved_root}\n"
                f"Current: {configured_root}"
            )

        if not saved_root.exists() or not saved_root.is_dir():
            raise StateRecoveryError(
                "Saved project root does not exist or is not a directory."
            )

        try:
            recovery_git = GitManager(str(saved_root))
        except (FileNotFoundError, NotADirectoryError, RuntimeError) as error:
            raise StateRecoveryError(
                f"Saved project is not a Git repository: {error}"
            ) from error

        head_result = recovery_git.head()
        if not head_result.success:
            raise StateRecoveryError(
                "Could not read repository HEAD:\n"
                f"{head_result}"
            )

        if head_result.stdout.strip() != state.starting_head:
            raise StateRecoveryError(
                "Recovery aborted: repository HEAD differs "
                "from saved checkpoint."
            )

        active_provider = self._provider_from_state(
            state.current_provider
        )

        self.git = recovery_git
        self.fs = FileSystem(str(saved_root))

        try:
            self._rollback(
                initial_untracked=set()
            )
        except RuntimeError as error:
            raise StateRecoveryError(
                f"Recovery aborted: rollback failed: {error}"
            ) from error
        initial_untracked: set[str] = set()

        already_recorded = (
            state.status == "interrupted"
            and active_provider is not None
            and bool(state.attempts)
            and Provider(state.attempts[-1].provider) == active_provider
            and state.attempts[-1].reason in {
                "worker interrupted",
                "reviewer interrupted",
                "interrupted during previous execution",
            }
            and not state.attempts[-1].success
        )
        if active_provider is not None and not already_recorded:
            (
                state.local_failures,
                state.deepseek_failures,
                state.codex_failures,
            ) = self._register_failure(
                active_provider,
                state.local_failures,
                state.deepseek_failures,
                state.codex_failures,
            )
            self._record_attempt(
                state=state,
                provider=active_provider,
                success=False,
                reason="interrupted during previous execution",
            )

        state.status = "running"
        state.current_provider = None
        self.state_manager.save(state)
        return self._run_loop(
            state=state,
            initial_untracked=initial_untracked,
            acceptance_criteria=state.acceptance_criteria,
            allowed_test_files=state.allowed_test_files,
        )

    def run_task(
        self,
        task: str,
        commit_message: str,
        allowed_test_files: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
    ) -> TaskResult:

        acceptance_criteria = list(
            acceptance_criteria or []
        )
        allowed_test_files = list(
            allowed_test_files or []
        )

        if not self.git.is_clean():
            return TaskResult(
                success=False,
                message=(
                    "Repository must be clean before starting a task."
                ),
                candidate=None,
                review=None,
                commit_hash=None,
                attempts=[],
            )

        initial_untracked = set(
            self.git.untracked_files()
        )
        starting_head_result = self.git.head()
        if not starting_head_result.success:
            raise RuntimeError(
                "Could not determine starting HEAD:\n"
                f"{starting_head_result}"
            )

        local_failures = 0
        deepseek_failures = 0
        codex_failures = 0
        attempts: list[AttemptResult] = []
        last_candidate: CandidateResult | None = None
        last_review: ReviewResult | None = None
        state = TaskState(
            version=1,
            status="running",
            task=task,
            project_root=self.project_root,
            commit_message=commit_message,
            starting_head=starting_head_result.stdout.strip(),
            current_provider=None,
            qwen_failures=0,  # Legacy serialized field for local failures.
            deepseek_failures=0,
            codex_failures=0,
            attempts=[],
            last_candidate=None,
            last_review=None,
            acceptance_criteria=acceptance_criteria,
            allowed_test_files=list(
                allowed_test_files or []
            ),
            budget_usage={
                "qwen_calls": self.initial_budget_usage.get(
                    "qwen_calls",
                    0,
                ),
                "deepseek_calls": self.initial_budget_usage.get(
                    "deepseek_calls",
                    0,
                ),
                "codex_calls": self.initial_budget_usage.get(
                    "codex_calls",
                    0,
                ),
            },
        )
        self.state_manager.save(state)

        return self._run_loop(
            state=state,
            initial_untracked=initial_untracked,
            acceptance_criteria=acceptance_criteria,
            allowed_test_files=allowed_test_files,
        )

    def _run_loop(
        self,
        state: TaskState,
        initial_untracked: set[str],
        acceptance_criteria: list[str],
        allowed_test_files: list[str],
    ) -> TaskResult:
        task = state.task
        commit_message = state.commit_message
        local_failures = state.local_failures
        deepseek_failures = state.deepseek_failures
        codex_failures = state.codex_failures
        budget = self._create_budget(state)
        attempts = [
            AttemptResult(
                provider=Provider(attempt.provider),
                success=attempt.success,
                reason=attempt.reason,
            )
            for attempt in state.attempts
        ]
        last_candidate = (
            CandidateResult(**state.last_candidate)
            if state.last_candidate is not None
            else None
        )
        last_review = (
            ReviewResult(**state.last_review)
            if state.last_review is not None
            else None
        )

        print("\n=== TASK START ===")
        print(task)

        while True:
            decision = self.router.route(
                task=task,
                local_failures=local_failures,
                deepseek_failures=deepseek_failures,
            )

            provider = decision.provider
            state.current_provider = provider.value
            self.state_manager.save(state)

            if not budget.can_use(provider):
                reason = (
                    f"Budget exhausted for {provider.value}: "
                    f"{budget.used(provider)}/"
                    f"{budget.limit(provider)} calls used."
                )
                self._rollback(initial_untracked)
                state.status = "failed"
                self.state_manager.save(state)
                return TaskResult(
                    success=False,
                    message=reason,
                    candidate=last_candidate,
                    review=last_review,
                    commit_hash=None,
                    attempts=attempts,
                )

            if (
                provider == Provider.CODEX
                and codex_failures >= 1
            ):
                self._rollback(initial_untracked)
                state.status = "failed"
                self.state_manager.save(state)
                return TaskResult(
                    success=False,
                    message=(
                        "All providers exhausted. "
                        "Codex failed the final escalation attempt."
                    ),
                    candidate=last_candidate,
                    review=last_review,
                    commit_hash=None,
                    attempts=attempts,
                )

            budget.consume(provider)
            self._save_budget(
                state,
                budget,
            )
            worker = self._create_worker(
                provider,
                acceptance_criteria=acceptance_criteria,
                allowed_test_files=allowed_test_files,
            )
            if provider == Provider.CODEX:
                reviewer = None
            else:
                reviewer = self._create_reviewer(
                    provider
                )
            review: ReviewResult | None = None

            print(
                "\n=== ROUTE ==="
            )
            print(
                f"Worker: {provider.value}"
            )
            print(
                f"Reason: {decision.reason}"
            )
            if reviewer is None:
                print(
                    "Reviewer: deterministic "
                    "validation only"
                )
            else:
                print(
                    "Reviewer:",
                    type(reviewer).__name__,
                )

            self._rollback(initial_untracked)

            try:
                if provider == Provider.CODEX:
                    candidate = worker.run_task(task)
                else:
                    candidate = worker.run(task)
            except KeyboardInterrupt:
                print(
                    "\nWorker interrupted — "
                    "rolling back."
                )
                (
                    local_failures,
                    deepseek_failures,
                    codex_failures,
                ) = self._register_failure(
                    provider,
                    local_failures,
                    deepseek_failures,
                    codex_failures,
                )
                state.local_failures = local_failures
                state.deepseek_failures = deepseek_failures
                state.codex_failures = codex_failures
                self.state_manager.save(state)
                attempts.append(
                    AttemptResult(
                        provider=provider,
                        success=False,
                        reason="worker interrupted",
                    )
                )
                self._record_attempt(
                    state=state,
                    provider=provider,
                    success=False,
                    reason="worker interrupted",
                )
                state.status = "interrupted"
                self.state_manager.save(state)
                self._rollback(initial_untracked)
                raise
            except Exception as error:
                print(
                    "\n=== WORKER ERROR ==="
                )
                print(
                    f"{type(error).__name__}: "
                    f"{error}"
                )
                (
                    local_failures,
                    deepseek_failures,
                    codex_failures,
                ) = self._register_failure(
                    provider,
                    local_failures,
                    deepseek_failures,
                    codex_failures,
                )
                state.local_failures = local_failures
                state.deepseek_failures = deepseek_failures
                state.codex_failures = codex_failures
                self.state_manager.save(state)
                reason = f"worker error: {type(error).__name__}"
                attempts.append(
                    AttemptResult(
                        provider=provider,
                        success=False,
                        reason=reason,
                    )
                )
                self._record_attempt(
                    state=state,
                    provider=provider,
                    success=False,
                    reason=reason,
                )
                self._rollback(initial_untracked)
                continue

            if not candidate.success:
                last_candidate = candidate
                last_review = None
                state.last_candidate = asdict(candidate)
                state.last_review = None
                self.state_manager.save(state)
                print(
                    "\n=== WORKER FAILED ==="
                )
                (
                    local_failures,
                    deepseek_failures,
                    codex_failures,
                ) = self._register_failure(
                    provider,
                    local_failures,
                    deepseek_failures,
                    codex_failures,
                )
                state.local_failures = local_failures
                state.deepseek_failures = deepseek_failures
                state.codex_failures = codex_failures
                self.state_manager.save(state)
                reason = "worker failed"
                attempts.append(
                    AttemptResult(
                        provider=provider,
                        success=False,
                        reason=reason,
                    )
                )
                self._record_attempt(
                    state=state,
                    provider=provider,
                    success=False,
                    reason=reason,
                )
                self._rollback(initial_untracked)
                continue

            last_candidate = candidate
            last_review = None
            state.last_candidate = asdict(candidate)
            state.last_review = None
            self.state_manager.save(state)

            print(
                "\n=== CANDIDATE READY ==="
            )

            if candidate.changed_files:
                print(
                    "Changed files:"
                )
                for path in candidate.changed_files:
                    print(
                        f"- {path}"
                    )

            policy_ok, policy_reason = (
                self._validate_change_policy(
                    candidate.changed_files,
                    allowed_test_files=allowed_test_files,
                )
            )

            if not policy_ok:
                reason = (
                    "Deterministic change policy rejected "
                    f"candidate: {policy_reason}"
                )

                print(
                    "\n=== CHANGE POLICY REJECTED ==="
                )
                print(reason)

                self._rollback(initial_untracked)

                (
                    local_failures,
                    deepseek_failures,
                    codex_failures,
                ) = self._register_failure(
                    provider,
                    local_failures,
                    deepseek_failures,
                    codex_failures,
                )
                state.local_failures = local_failures
                state.deepseek_failures = deepseek_failures
                state.codex_failures = codex_failures
                self.state_manager.save(state)
                attempts.append(
                    AttemptResult(
                        provider=provider,
                        success=False,
                        reason=reason,
                    )
                )
                self._record_attempt(
                    state=state,
                    provider=provider,
                    success=False,
                    reason=reason,
                )
                continue

            if reviewer is not None:
                reviewer_provider = (
                    Provider.DEEPSEEK
                    if provider == Provider.LOCAL
                    else Provider.CODEX
                )
                if not budget.can_use(reviewer_provider):
                    reason = (
                        "Budget exhausted for "
                        f"{reviewer_provider.value}: "
                        f"{budget.used(reviewer_provider)}/"
                        f"{budget.limit(reviewer_provider)} "
                        "calls used; candidate cannot be reviewed."
                    )
                    self._rollback(initial_untracked)
                    (
                        local_failures,
                        deepseek_failures,
                        codex_failures,
                    ) = self._register_failure(
                        provider,
                        local_failures,
                        deepseek_failures,
                        codex_failures,
                    )
                    state.local_failures = local_failures
                    state.deepseek_failures = deepseek_failures
                    state.codex_failures = codex_failures
                    state.status = "failed"
                    state.last_candidate = asdict(candidate)
                    state.last_review = None
                    state.current_provider = None
                    attempts.append(
                        AttemptResult(
                            provider=provider,
                            success=False,
                            reason=reason,
                        )
                    )
                    self._record_attempt(
                        state=state,
                        provider=provider,
                        success=False,
                        reason=reason,
                    )
                    return TaskResult(
                        success=False,
                        message=reason,
                        candidate=candidate,
                        review=None,
                        commit_hash=None,
                        attempts=attempts,
                    )

                budget.consume(reviewer_provider)
                self._save_budget(
                    state,
                    budget,
                )
                print(
                    "\n=== REVIEW ==="
                )

                try:
                    review = reviewer.review(
                        state.task,
                        candidate,
                        acceptance_criteria=acceptance_criteria,
                        allowed_test_files=allowed_test_files,
                    )
                except KeyboardInterrupt:
                    print(
                        "\nReviewer interrupted — "
                        "rolling back."
                    )
                    (
                        local_failures,
                        deepseek_failures,
                        codex_failures,
                    ) = self._register_failure(
                        provider,
                        local_failures,
                        deepseek_failures,
                        codex_failures,
                    )
                    state.local_failures = local_failures
                    state.deepseek_failures = deepseek_failures
                    state.codex_failures = codex_failures
                    self.state_manager.save(state)
                    attempts.append(
                        AttemptResult(
                            provider=provider,
                            success=False,
                            reason="reviewer interrupted",
                        )
                    )
                    self._record_attempt(
                        state=state,
                        provider=provider,
                        success=False,
                        reason="reviewer interrupted",
                    )
                    state.status = "interrupted"
                    self.state_manager.save(state)
                    self._rollback(initial_untracked)
                    raise
                except Exception as error:
                    print(
                        "\n=== REVIEWER ERROR ==="
                    )
                    print(
                        f"{type(error).__name__}: "
                        f"{error}"
                    )
                    (
                        local_failures,
                        deepseek_failures,
                        codex_failures,
                    ) = self._register_failure(
                        provider,
                        local_failures,
                        deepseek_failures,
                        codex_failures,
                    )
                    state.local_failures = local_failures
                    state.deepseek_failures = deepseek_failures
                    state.codex_failures = codex_failures
                    self.state_manager.save(state)
                    reason = f"reviewer error: {type(error).__name__}"
                    attempts.append(
                        AttemptResult(
                            provider=provider,
                            success=False,
                            reason=reason,
                        )
                    )
                    self._record_attempt(
                        state=state,
                        provider=provider,
                        success=False,
                        reason=reason,
                    )
                    self._rollback(initial_untracked)
                    continue

                last_review = review
                state.last_review = asdict(review)
                self.state_manager.save(state)
                print(
                    f"Approved: {review.approved}"
                )
                print(
                    f"Reason: {review.reason}"
                )

                if review.issues:
                    print("\nIssues:")
                    for issue in review.issues:
                        print(
                            f"- {issue}"
                        )

                if not review.approved:
                    print(
                        "\n=== CANDIDATE REJECTED ==="
                    )
                    (
                        local_failures,
                        deepseek_failures,
                        codex_failures,
                    ) = self._register_failure(
                        provider,
                        local_failures,
                        deepseek_failures,
                        codex_failures,
                    )
                    state.local_failures = local_failures
                    state.deepseek_failures = deepseek_failures
                    state.codex_failures = codex_failures
                    self.state_manager.save(state)
                    attempts.append(
                        AttemptResult(
                            provider=provider,
                            success=False,
                            reason="reviewer rejected",
                        )
                    )
                    self._record_attempt(
                        state=state,
                        provider=provider,
                        success=False,
                        reason="reviewer rejected",
                    )
                    self._rollback(initial_untracked)
                    continue

            print(
                "\n=== FINAL VALIDATION ==="
            )
            final_tests = worker.tests.run_pytest()
            print(final_tests)

            if not final_tests.success:
                print(
                    "\nFinal tests failed. "
                    "Rolling back."
                )
                (
                    local_failures,
                    deepseek_failures,
                    codex_failures,
                ) = self._register_failure(
                    provider,
                    local_failures,
                    deepseek_failures,
                    codex_failures,
                )
                state.local_failures = local_failures
                state.deepseek_failures = deepseek_failures
                state.codex_failures = codex_failures
                self.state_manager.save(state)
                attempts.append(
                    AttemptResult(
                        provider=provider,
                        success=False,
                        reason="final tests failed",
                    )
                )
                self._record_attempt(
                    state=state,
                    provider=provider,
                    success=False,
                    reason="final tests failed",
                )
                self._rollback(initial_untracked)
                continue

            final_changed_files = self.git.changed_files()
            policy_ok, policy_reason = (
                self._validate_change_policy(
                    final_changed_files,
                    allowed_test_files=allowed_test_files,
                )
            )

            if not policy_ok:
                reason = (
                    "Final deterministic change policy "
                    f"rejected candidate: {policy_reason}"
                )

                print(
                    "\n=== FINAL CHANGE POLICY REJECTED ==="
                )
                print(reason)

                self._rollback(initial_untracked)

                (
                    local_failures,
                    deepseek_failures,
                    codex_failures,
                ) = self._register_failure(
                    provider,
                    local_failures,
                    deepseek_failures,
                    codex_failures,
                )
                state.local_failures = local_failures
                state.deepseek_failures = deepseek_failures
                state.codex_failures = codex_failures
                self.state_manager.save(state)
                attempts.append(
                    AttemptResult(
                        provider=provider,
                        success=False,
                        reason=reason,
                    )
                )
                self._record_attempt(
                    state=state,
                    provider=provider,
                    success=False,
                    reason=reason,
                )
                continue

            add_result = self.git.add_all()
            if not add_result.success:
                (
                    local_failures,
                    deepseek_failures,
                    codex_failures,
                ) = self._register_failure(
                    provider,
                    local_failures,
                    deepseek_failures,
                    codex_failures,
                )
                state.local_failures = local_failures
                state.deepseek_failures = deepseek_failures
                state.codex_failures = codex_failures
                self.state_manager.save(state)
                attempts.append(
                    AttemptResult(
                        provider=provider,
                        success=False,
                        reason="git add failed",
                    )
                )
                self._record_attempt(
                    state=state,
                    provider=provider,
                    success=False,
                    reason="git add failed",
                )
                self._rollback(initial_untracked)
                state.status = "failed"
                self.state_manager.save(state)
                raise RuntimeError(
                    "git add failed:\n"
                    f"{add_result}"
                )

            commit_result = self.git.commit(commit_message)
            if not commit_result.success:
                (
                    local_failures,
                    deepseek_failures,
                    codex_failures,
                ) = self._register_failure(
                    provider,
                    local_failures,
                    deepseek_failures,
                    codex_failures,
                )
                state.local_failures = local_failures
                state.deepseek_failures = deepseek_failures
                state.codex_failures = codex_failures
                self.state_manager.save(state)
                attempts.append(
                    AttemptResult(
                        provider=provider,
                        success=False,
                        reason="git commit failed",
                    )
                )
                self._record_attempt(
                    state=state,
                    provider=provider,
                    success=False,
                    reason="git commit failed",
                )
                self._rollback(initial_untracked)
                state.status = "failed"
                self.state_manager.save(state)
                raise RuntimeError(
                    "git commit failed:\n"
                    f"{commit_result}"
                )

            head_result = self.git.head()
            if not head_result.success:
                raise RuntimeError(
                    "Commit was created but HEAD could not be retrieved."
                )

            commit_hash = head_result.stdout.strip()
            attempts.append(
                AttemptResult(
                    provider=provider,
                    success=True,
                    reason="accepted and committed",
                )
            )
            state.commit_hash = commit_hash
            state.status = "completed"
            state.current_provider = None
            self.state_manager.save(state)
            self._record_attempt(
                state=state,
                provider=provider,
                success=True,
                reason="accepted and committed",
            )

            print(
                "\n=== TASK COMPLETED ==="
            )
            print(
                f"Commit: {commit_hash}"
            )

            if review is None:
                message = (
                    "Task completed, validated and committed."
                )
            else:
                message = (
                    "Task completed, reviewed, validated "
                    "and committed."
                )

            return TaskResult(
                success=True,
                message=message,
                candidate=candidate,
                review=review,
                commit_hash=commit_hash,
                attempts=attempts,
            )
