"""Reviewer session service — spawns LLM-powered review sessions for raids.

When a raid enters REVIEW, the ReviewEngine delegates to this service to spawn
a lightweight reviewer session (using the skuld-planner chart). The reviewer
reads the PR diff, checks project rules, scores confidence, and reports back.

The reviewer session replaces deterministic signal-based review with an
LLM-powered review that understands code context, architecture, and quality.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from tyr.config import ReviewConfig
from tyr.domain.models import PRStatus, Raid
from tyr.ports.volundr import SpawnRequest, VolundrFactory, VolundrPort, VolundrSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewerResult:
    """Outcome of a reviewer session."""

    session_id: str
    confidence: float
    summary: str
    findings: list[str]
    approved: bool


def _build_acceptance_criteria_section(raid: Raid) -> str:
    if not raid.acceptance_criteria:
        return ""
    lines = ["**Acceptance Criteria**:"]
    for criterion in raid.acceptance_criteria:
        lines.append(f"- {criterion}")
    return "\n".join(lines) + "\n\n"


def _build_pr_section(pr_status: PRStatus | None) -> str:
    if not pr_status:
        return ""
    return (
        f"**PR**: {pr_status.url}\n"
        f"**PR State**: {pr_status.state}\n"
        f"**CI Passed**: {pr_status.ci_passed}\n"
        f"**Mergeable**: {pr_status.mergeable}\n\n"
    )


def _build_changed_files_section(changed_files: list[str]) -> str:
    if not changed_files:
        return ""
    lines = [f"**Changed Files** ({len(changed_files)}):"]
    for f in changed_files:
        lines.append(f"- `{f}`")
    return "\n".join(lines) + "\n\n"


def _build_diff_summary_section(diff_summary: str) -> str:
    if not diff_summary:
        return ""
    return f"**Diff Summary**:\n{diff_summary}\n\n"


def _build_review_loop_section(
    working_session_id: str,
    max_review_rounds: int,
) -> str:
    if not working_session_id:
        return ""
    return (
        "## Review Loop\n"
        "\n"
        "You have direct access to the working session that produced this code.\n"
        f"Working Session ID: `{working_session_id}`\n"
        f"Max review rounds: {max_review_rounds}\n"
        "\n"
        "When you find blocking issues:\n"
        "\n"
        "1. Discover your own session ID:\n"
        "   `MY_ID=$(basename $(dirname /volundr/sessions/*/workspace))`\n"
        "2. Send detailed feedback to the working session:\n"
        f"   `curl -s -X POST http://localhost:8081/api/message "
        f'-H "Content-Type: application/json" '
        f'-d \'{{"session_id": "{working_session_id}", "content": "<FEEDBACK>"}}\'`\n'
        "3. In your feedback, tell the working session to:\n"
        "   a. Fix the issues\n"
        "   b. `git add` and `git commit` the fixes\n"
        "   c. `git push` to update the PR\n"
        "   d. Notify you when done by running:\n"
        "      `curl -s -X POST http://localhost:8081/api/message "
        '-H "Content-Type: application/json" '
        '-d \'{"session_id": "<YOUR_SESSION_ID>",'
        ' "content": "Fixed. Please pull and re-review."}\'`\n'
        "      where <YOUR_SESSION_ID> is `$MY_ID`\n"
        "4. When the working session responds, run `git pull` to get the latest changes\n"
        "5. Re-read the diff and re-review\n"
        "6. Repeat until no blocking issues remain or you exhaust all review rounds\n"
        f"7. After {max_review_rounds} rounds with unresolved blocking issues, set approved=false\n"
        "\n"
    )


def build_reviewer_initial_prompt(
    raid: Raid,
    pr_status: PRStatus | None,
    changed_files: list[str],
    diff_summary: str,
    working_session_id: str = "",
    max_review_rounds: int = 6,
    template: str = "",
) -> str:
    """Build the initial prompt sent to the reviewer session.

    If a template is provided (from config), it is used with dynamic sections
    injected via placeholders. Otherwise a minimal fallback is used.
    """
    sections = {
        "tracker_id": raid.tracker_id,
        "raid_name": raid.name,
        "raid_description": raid.description,
        "acceptance_criteria_section": _build_acceptance_criteria_section(raid),
        "pr_section": _build_pr_section(pr_status),
        "changed_files_section": _build_changed_files_section(changed_files),
        "diff_summary_section": _build_diff_summary_section(diff_summary),
        "review_loop_section": _build_review_loop_section(working_session_id, max_review_rounds),
    }

    if template:
        return template.format(**sections)

    # Minimal fallback when no template is configured.
    return (
        f"## Review Request\n\n"
        f"**Ticket**: {raid.tracker_id}\n"
        f"**Raid**: {raid.name}\n"
        f"**Description**: {raid.description}\n\n"
        f"{sections['acceptance_criteria_section']}"
        f"{sections['pr_section']}"
        f"{sections['changed_files_section']}"
        f"{sections['diff_summary_section']}"
        f"{sections['review_loop_section']}"
        "Review the PR and output your assessment as JSON."
    )


def _try_parse_json(text: str) -> ReviewerResult | None:
    """Attempt to parse the reviewer response as JSON."""
    # Extract JSON from markdown code fences if present
    cleaned = text.strip()
    for prefix in ("```json", "```"):
        if prefix in cleaned:
            start = cleaned.index(prefix) + len(prefix)
            end = cleaned.index("```", start) if "```" in cleaned[start:] else len(cleaned)
            cleaned = cleaned[start:end].strip()
            break

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    confidence = data.get("confidence", 0.0)
    if not isinstance(confidence, (int, float)):
        return None
    confidence = max(0.0, min(1.0, float(confidence)))

    approved = bool(data.get("approved", False))
    summary = str(data.get("summary", ""))

    # Accept "findings" (new format) or "issues" (legacy) or both merged
    findings: list[str] = []
    for key in ("findings", "issues", "nits", "improvements"):
        raw = data.get(key, [])
        if isinstance(raw, list):
            findings.extend(str(i) for i in raw)

    if not summary and confidence == 0.0:
        return None

    return ReviewerResult(
        session_id="",
        confidence=confidence,
        summary=summary,
        findings=findings,
        approved=approved,
    )


def _try_parse_text(text: str) -> ReviewerResult | None:
    """Parse the text-based CONFIDENCE:/APPROVED:/SUMMARY:/FINDINGS: format."""
    confidence = 0.0
    approved = False
    summary = ""
    findings: list[str] = []
    in_findings = False

    for line in text.splitlines():
        stripped = line.strip()

        if stripped.upper().startswith("CONFIDENCE:"):
            try:
                confidence = float(stripped.split(":", 1)[1].strip())
                confidence = max(0.0, min(1.0, confidence))
            except (ValueError, IndexError):
                pass
            in_findings = False
            continue

        if stripped.upper().startswith("APPROVED:"):
            val = stripped.split(":", 1)[1].strip().lower()
            approved = val in ("yes", "true", "1")
            in_findings = False
            continue

        if stripped.upper().startswith("SUMMARY:"):
            summary = stripped.split(":", 1)[1].strip()
            in_findings = False
            continue

        if stripped.upper().startswith(("FINDINGS:", "ISSUES:")):
            in_findings = True
            continue

        if in_findings and stripped.startswith("- "):
            findings.append(stripped[2:].strip())
            continue

    if not summary and confidence == 0.0:
        return None

    return ReviewerResult(
        session_id="",
        confidence=confidence,
        summary=summary,
        findings=findings,
        approved=approved,
    )


def parse_reviewer_response(text: str) -> ReviewerResult | None:
    """Parse the structured response from a reviewer session.

    Tries JSON first, falls back to the text-based format.
    Returns None if the response cannot be parsed.
    """
    result = _try_parse_json(text)
    if result is not None:
        return result
    return _try_parse_text(text)


class ReviewerSessionService:
    """Spawns and manages LLM-powered reviewer sessions.

    Responsibilities:
    - Build the reviewer system prompt and initial prompt
    - Spawn a reviewer session via Volundr
    - Parse the reviewer's confidence score and feedback
    - Send feedback to the working session when issues are found
    """

    def __init__(
        self,
        volundr_factory: VolundrFactory,
        review_config: ReviewConfig,
    ) -> None:
        self._volundr_factory = volundr_factory
        self._cfg = review_config

    async def spawn_reviewer(
        self,
        raid: Raid,
        owner_id: str,
        pr_status: PRStatus | None,
        changed_files: list[str],
        integration_ids: list[str] | None = None,
        working_session: VolundrSession | None = None,
    ) -> VolundrSession | None:
        """Spawn a reviewer session for a raid in REVIEW state.

        Returns the VolundrSession if spawned successfully, None otherwise.
        """
        adapters = await self._volundr_factory.for_owner(owner_id)
        if not adapters:
            logger.error(
                "No authenticated Volundr adapter for owner %s — "
                "user must configure a CODE_FORGE integration with a valid PAT",
                owner_id[:8],
            )
            return None

        # Find the Volundr instance hosting the working session
        volundr = adapters[0]
        if raid.session_id and len(adapters) > 1:
            for adapter in adapters:
                try:
                    session = await adapter.get_session(raid.session_id)
                    if session is not None:
                        volundr = adapter
                        break
                except Exception:
                    continue

        if not integration_ids:
            integration_ids = await self._resolve_integration_ids(
                volundr=volundr,
                owner_id=owner_id,
            )

        diff_summary = self._get_diff_summary(raid)

        initial_prompt = build_reviewer_initial_prompt(
            raid=raid,
            pr_status=pr_status,
            changed_files=changed_files,
            diff_summary=diff_summary,
            working_session_id=working_session.id if working_session else "",
            max_review_rounds=self._cfg.max_review_rounds,
            template=self._cfg.reviewer_initial_prompt_template,
        )

        request = SpawnRequest(
            name=f"review-{(raid.identifier or raid.tracker_id[:8]).lower()}",
            repo=working_session.repo if working_session else "",
            branch=working_session.name if working_session else "",
            base_branch=working_session.base_branch if working_session else "",
            model=self._cfg.reviewer_model,
            tracker_issue_id=raid.identifier or raid.tracker_id,
            tracker_issue_url=raid.url or raid.pr_url or "",
            system_prompt=self._cfg.reviewer_system_prompt,
            initial_prompt=initial_prompt,
            workload_type="reviewer",
            profile=self._cfg.reviewer_profile,
            integration_ids=integration_ids,
        )

        logger.info(
            "Spawning reviewer: repo=%s branch=%s model=%s profile=%s integrations=%d",
            working_session.repo if working_session else "?",
            working_session.branch if working_session else "?",
            self._cfg.reviewer_model,
            self._cfg.reviewer_profile,
            len(integration_ids),
        )
        try:
            session = await volundr.spawn_session(request)
            logger.info(
                "Spawned reviewer session %s for raid %s",
                session.id,
                raid.tracker_id,
            )
            return session
        except Exception:
            logger.warning(
                "Failed to spawn reviewer session for raid %s",
                raid.tracker_id,
                exc_info=True,
            )
            return None

    async def _resolve_integration_ids(
        self,
        *,
        volundr: VolundrPort,
        owner_id: str,
    ) -> list[str]:
        """Resolve enabled integration IDs for reviewer session spawns."""
        try:
            ids = await volundr.list_integration_ids()
            logger.info(
                "Resolved %d integration IDs for reviewer session owner %s",
                len(ids),
                owner_id[:8],
            )
            return ids
        except Exception:
            logger.warning(
                "Failed to resolve Volundr integrations for reviewer owner %s",
                owner_id[:8],
                exc_info=True,
            )
            return []

    async def send_feedback_to_working_session(
        self,
        raid: Raid,
        owner_id: str,
        result: ReviewerResult,
    ) -> None:
        """Send reviewer feedback to the working session that produced the PR."""
        if not raid.session_id:
            return

        if not result.findings:
            return

        adapters = await self._volundr_factory.for_owner(owner_id)
        if not adapters:
            logger.warning(
                "No authenticated Volundr adapter for owner %s — cannot send feedback",
                owner_id[:8],
            )
            return

        volundr = adapters[0]

        feedback_lines = [
            f"## Review Feedback (confidence: {result.confidence:.2f})",
            "",
            f"**Summary**: {result.summary}",
            "",
            "**Findings to address** (all must be fixed before merge):",
        ]
        for finding in result.findings:
            feedback_lines.append(f"- {finding}")
        feedback_lines.extend(
            [
                "",
                "After fixing, `git add`, `git commit`, and `git push` your changes,",
                "then notify the reviewer that the fixes are ready for re-review.",
            ]
        )

        try:
            await volundr.send_message(raid.session_id, "\n".join(feedback_lines))
            logger.info(
                "Sent reviewer feedback to session %s (%d findings)",
                raid.session_id,
                len(result.findings),
            )
        except Exception:
            logger.warning(
                "Failed to send reviewer feedback to session %s",
                raid.session_id,
                exc_info=True,
            )

    def _get_diff_summary(self, raid: Raid) -> str:
        """Get a diff summary from the raid's chronicle summary."""
        if not raid.chronicle_summary:
            return ""
        return raid.chronicle_summary
