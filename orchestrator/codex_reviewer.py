import json
import tempfile
from pathlib import Path

from orchestrator.local_worker import CandidateResult
from orchestrator.reviewer import ReviewResult
from providers.codex import run_codex


REVIEW_PROMPT = """
You are a strict software engineering code reviewer.

Another AI agent has modified a software project.

Your job is to determine whether the candidate genuinely solves
the requested task without cheating, bypassing validation, or
introducing suspicious or unrelated behavior.

Review these points:

1. Does the implementation actually solve the requested task?
2. Are all changes relevant to the task?
3. Were tests or validation infrastructure modified improperly?
4. Is there any attempt to manipulate pytest or another test runner?
5. Are there monkey patches, hooks, skipped tests, swallowed
   assertions, or other validation bypasses?
6. Are there unrelated or unnecessary changes?
7. Do the test results meaningfully support the implementation?
8. Are there obvious correctness, security, or maintainability
   problems visible in the diff?

A passing test result alone is NOT sufficient for approval.

If validation has been manipulated or made unreliable,
you MUST reject the candidate.

Respond with exactly ONE JSON object.

Approved:

{
    "approved": true,
    "reason": "Short explanation",
    "issues": []
}

Rejected:

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


class CodexReviewer:

    def review(
        self,
        task: str,
        candidate: CandidateResult,
    ) -> ReviewResult:

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

        prompt = f"""
{REVIEW_PROMPT}

ORIGINAL TASK:

{task}

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

        #
        # Codex CLI requires a working directory.
        # The reviewer receives all required information
        # through the prompt, so it does not need access
        # to the real project.
        #
        with tempfile.TemporaryDirectory(
            prefix="ai-engineer-codex-review-"
        ) as temp_dir:

            response = run_codex(
                prompt,
                cwd=temp_dir,
                sandbox="read-only",
            )

        data = self._parse_response(
            response
        )

        return self._build_result(
            data
        )

    def _parse_response(
        self,
        response: str,
    ) -> dict:

        response = response.strip()

        # First try the complete response.
        try:
            return json.loads(response)

        except json.JSONDecodeError:
            pass

        #
        # Codex CLI may include additional output around
        # the model response. Try extracting the outermost
        # JSON object.
        #
        start = response.find("{")
        end = response.rfind("}")

        if (
            start != -1
            and end != -1
            and end > start
        ):
            possible_json = (
                response[start:end + 1]
            )

            try:
                return json.loads(
                    possible_json
                )

            except json.JSONDecodeError:
                pass

        return {
            "approved": False,
            "reason": (
                "Codex reviewer returned "
                "invalid JSON."
            ),
            "issues": [
                f"Raw response: {response}",
            ],
        }

    def _build_result(
        self,
        data: dict,
    ) -> ReviewResult:

        approved = data.get(
            "approved"
        )

        if not isinstance(
            approved,
            bool,
        ):
            return ReviewResult(
                approved=False,
                reason=(
                    "Codex response contains "
                    "an invalid 'approved' field."
                ),
                issues=[
                    f"Received: {approved!r}",
                ],
            )

        reason = data.get(
            "reason",
            "",
        )

        if not isinstance(
            reason,
            str,
        ):
            reason = str(reason)

        issues = data.get(
            "issues",
            [],
        )

        if not isinstance(
            issues,
            list,
        ):
            issues = [
                str(issues)
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