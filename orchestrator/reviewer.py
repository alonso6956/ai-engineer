from orchestrator.events import activity
import json
from dataclasses import dataclass

from providers.deepseek import run_deepseek
from orchestrator.local_worker import CandidateResult


@dataclass
class ReviewResult:
    approved: bool
    reason: str
    issues: list[str]


REVIEW_SYSTEM_PROMPT = """
You are a strict software engineering code reviewer.

Another AI agent has modified a software project.

Your job is to determine whether the candidate genuinely solves
the requested task without cheating, bypassing validation, or
introducing suspicious behavior.

You are NOT allowed to modify the project.

Review the following:

1. Does the implementation actually solve the requested task?
2. Are the changes relevant to the task?
3. Did the worker modify tests or validation infrastructure?
4. Did the worker attempt to manipulate pytest or the test runner?
5. Are there suspicious monkey patches, hooks, imports, or runtime
   modifications intended to make tests pass artificially?
6. Did the worker introduce unrelated changes?
7. Do the reported tests support the implementation?
8. Are there obvious correctness problems in the diff?

IMPORTANT:

A passing test result alone is NOT sufficient for approval.

If the implementation manipulates the testing framework, disables
tests, skips tests, catches assertions, monkey-patches test behavior,
or otherwise makes validation unreliable, you MUST reject it.

Respond with exactly ONE valid JSON object.

Approved response format:

{
    "approved": true,
    "reason": "Short explanation",
    "issues": []
}

Rejected response format:

{
    "approved": false,
    "reason": "Short explanation",
    "issues": [
        "Issue 1",
        "Issue 2"
    ]
}

Do not use Markdown.
Do not use code fences.
Do not include text outside the JSON object.
"""


class DeepSeekReviewer:

    @activity('DeepSeek review')
    def review(
        self,
        task: str,
        candidate: CandidateResult,
        acceptance_criteria: list[str] | None = None,
        allowed_test_files: list[str] | None = None,
    ) -> ReviewResult:

        acceptance_criteria = list(
            acceptance_criteria or []
        )
        allowed_test_files = list(
            allowed_test_files or []
        )

        if not candidate.success:
            return ReviewResult(
                approved=False,
                reason=(
                    "The worker did not produce "
                    "a successful candidate."
                ),
                issues=[
                    candidate.message,
                ],
            )

        changed_files = "\n".join(
            f"- {path}"
            for path in candidate.changed_files
        )

        if not changed_files:
            changed_files = "(none)"

        criteria_text = "\n".join(
            f"- {criterion}"
            for criterion in acceptance_criteria
        )
        criteria_text = criteria_text or "- None specified."
        allowed_tests_text = "\n".join(
            f"- {path}"
            for path in allowed_test_files
        )
        if not allowed_tests_text:
            allowed_tests_text = "- None"

        prompt = f"""
{REVIEW_SYSTEM_PROMPT}

ORIGINAL TASK:

{task}

ACCEPTANCE CRITERIA:
{criteria_text}

Evaluate the candidate against every acceptance criterion.
Reject the candidate if any criterion is demonstrably unsatisfied.
Do not reject solely because a criterion cannot be demonstrated from the diff.
Use available code, tests, and evidence; do not invent requirements beyond the task and acceptance criteria.

AUTHORIZED TEST FILES:
{allowed_tests_text}

Test changes are permitted only for the exact files listed above.
An authorized test-file modification is not, by itself, a reason to reject the candidate.
Reject test changes outside the authorized list.
Validation infrastructure such as conftest.py, pytest.ini, tox.ini, .git/*,
and .github/* must not be modified.
Reject attempts to weaken, skip, bypass, monkeypatch, or disable validation,
even inside an authorized test file.

This review is a second layer. ChangePolicy remains the deterministic authority
for enforcing changed-file permissions.

CHANGED FILES:

{changed_files}

GIT STATUS:

{candidate.git_status}

TEST RESULTS:

{candidate.tests_output}

GIT DIFF:

{candidate.diff}

Review the candidate now.
"""

        response = run_deepseek(prompt)

        try:
            data = json.loads(response)
        except json.JSONDecodeError as error:
            return ReviewResult(
                approved=False,
                reason=(
                    "DeepSeek returned invalid JSON."
                ),
                issues=[
                    str(error),
                    f"Raw response: {response}",
                ],
            )

        approved = data.get("approved")

        if not isinstance(approved, bool):
            return ReviewResult(
                approved=False,
                reason=(
                    "Reviewer response contains an "
                    "invalid 'approved' field."
                ),
                issues=[
                    f"Received: {approved!r}",
                ],
            )

        reason = data.get("reason", "")

        if not isinstance(reason, str):
            reason = str(reason)

        issues = data.get("issues", [])

        if not isinstance(issues, list):
            issues = [
                str(issues),
            ]

        issues = [
            str(issue)
            for issue in issues
        ]

        return ReviewResult(
            approved=approved,
            reason=reason,
            issues=issues,
        )