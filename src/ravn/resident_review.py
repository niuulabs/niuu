"""Resident self-review and verification runtime."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
    ResidentWorkItemBackend,
)
from ravn.domain.resident_review import (
    ResidentArtifactReview,
    ResidentReviewDecision,
    ResidentReviewReport,
    ResidentReviewTarget,
    ResidentVerificationEvidence,
)
from ravn.ports.mimir import MimirPort
from ravn.ports.resident_review import ResidentVerificationPort

_REVIEW_PREFIX = "resident/reviews"
_REVIEW_AUDIT_PREFIX = "resident/reviews/audits"


@dataclass(frozen=True)
class ResidentReviewRuntimeConfig:
    """Bounds for one resident review pass."""

    max_follow_up_objectives: int = 1
    duplicate_review_enabled: bool = False


class ResidentReviewMemoryPort(Protocol):
    """Persistence boundary for resident review artifacts."""

    async def list_reviews(self, review_key: str = "") -> list[ResidentArtifactReview]:
        """List prior artifact reviews, optionally filtered by review key."""

    async def write_review(self, review: ResidentArtifactReview) -> str:
        """Persist one artifact review."""

    async def write_audit(self, content: str) -> str:
        """Persist one review audit record."""


class LocalResidentReviewMemory(ResidentReviewMemoryPort):
    """Filesystem-backed review memory using the Mimir page shape."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    async def list_reviews(self, review_key: str = "") -> list[ResidentArtifactReview]:
        base = self._root / _REVIEW_PREFIX
        if not base.exists():
            return []
        reviews: list[ResidentArtifactReview] = []
        for path in sorted(base.glob("*.md")):
            parsed = _parse_review(path.read_text(encoding="utf-8"))
            if parsed is None:
                continue
            if review_key and parsed.review_key != review_key:
                continue
            reviews.append(parsed)
        return reviews

    async def write_review(self, review: ResidentArtifactReview) -> str:
        rel = Path(_REVIEW_PREFIX) / f"{review.id}.md"
        return self._write(rel, _render_review(review))

    async def write_audit(self, content: str) -> str:
        rel = Path(_REVIEW_AUDIT_PREFIX) / f"{_stamp(datetime.now(UTC))}.md"
        return self._write(rel, content)

    def _write(self, rel: Path, content: str) -> str:
        path = self._root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(rel)


class MimirResidentReviewMemory(ResidentReviewMemoryPort):
    """Mimir-backed review memory."""

    def __init__(self, mimir: MimirPort) -> None:
        self._mimir = mimir

    async def list_reviews(self, review_key: str = "") -> list[ResidentArtifactReview]:
        pages = await self._mimir.list_pages(prefix=_REVIEW_PREFIX)
        reviews: list[ResidentArtifactReview] = []
        for meta in sorted(pages, key=lambda page: getattr(page, "path", "")):
            path = str(getattr(meta, "path", "") or "")
            if "/audits/" in path:
                continue
            try:
                content = await self._mimir.read_page(path)
            except FileNotFoundError:
                continue
            parsed = _parse_review(content)
            if parsed is None:
                continue
            if review_key and parsed.review_key != review_key:
                continue
            reviews.append(parsed)
        return reviews

    async def write_review(self, review: ResidentArtifactReview) -> str:
        path = f"{_REVIEW_PREFIX}/{review.id}.md"
        await self._mimir.upsert_page(path, _render_review(review))
        return path

    async def write_audit(self, content: str) -> str:
        path = f"{_REVIEW_AUDIT_PREFIX}/{_stamp(datetime.now(UTC))}.md"
        await self._mimir.upsert_page(path, content)
        return path


class ResidentReviewRuntime:
    """Run concrete checks before resident work is treated as trusted."""

    def __init__(
        self,
        *,
        backend: ResidentWorkItemBackend,
        memory: ResidentReviewMemoryPort,
        verifier: ResidentVerificationPort,
        config: ResidentReviewRuntimeConfig | None = None,
    ) -> None:
        self._backend = backend
        self._memory = memory
        self._verifier = verifier
        self._config = config or ResidentReviewRuntimeConfig()

    async def review(self, mandate: str, target: ResidentReviewTarget) -> ResidentReviewReport:
        review_key = _review_key(target)
        prior = await self._memory.list_reviews(review_key)
        if prior and not self._config.duplicate_review_enabled:
            duplicate = prior[-1]
            review = ResidentArtifactReview(
                id=_review_id(target, prefix="duplicate"),
                review_key=review_key,
                target=target,
                decision=ResidentReviewDecision.SKIPPED_DUPLICATE.value,
                reason=f"review key already covered by {duplicate.id}",
                duplicate_of=duplicate.id,
            )
            review_ref = await self._memory.write_review(review)
            audit_ref = await self._memory.write_audit(
                _render_audit(
                    "Duplicate Resident Review Skipped",
                    f"Skipped duplicate review for key `{review_key}`.",
                    refs=(review_ref,),
                )
            )
            return ResidentReviewReport(
                mandate=mandate,
                target=target,
                review=review,
                persisted_refs=(review_ref, audit_ref),
                duplicate_skipped=True,
                final_suggested_next_action="Reuse the prior review evidence.",
            )

        evidence = tuple([await self._verifier.verify(check) for check in target.checks])
        failed = tuple(item for item in evidence if item.status != "passed")
        decision = (
            ResidentReviewDecision.FAILED.value
            if failed
            else ResidentReviewDecision.PASSED.value
        )
        reason = _review_reason(decision, evidence)
        review = ResidentArtifactReview(
            id=_review_id(target),
            review_key=review_key,
            target=target,
            decision=decision,
            reason=reason,
            evidence=evidence,
        )
        if (
            decision == ResidentReviewDecision.FAILED.value
            and self._config.max_follow_up_objectives > 0
        ):
            review = replace(
                review,
                follow_up_objective_id=_follow_up_objective_id(target, review),
            )
        review_ref = await self._memory.write_review(review)
        refs = [review_ref]
        follow_up = await self._handle_portfolio_after_review(mandate, target, review, review_ref)
        refs.extend(follow_up)
        decision_ref = await self._backend.append_decision(
            mandate,
            (
                f"{datetime.now(UTC).isoformat()} [resident_review] "
                f"target={target.id} decision={decision} review_ref={review_ref}"
            ),
        )
        refs.append(decision_ref)
        audit_ref = await self._memory.write_audit(
            _render_audit(
                "Resident Artifact Review",
                f"{target.title}: {decision}. {reason}",
                refs=tuple(refs),
            )
        )
        refs.append(audit_ref)
        created_follow_up_id = review.follow_up_objective_id
        updated_objective_id = (
            target.source_objective_id
            if decision == ResidentReviewDecision.PASSED.value
            and target.complete_objective_on_pass
            else ""
        )
        return ResidentReviewReport(
            mandate=mandate,
            target=target,
            review=review,
            persisted_refs=tuple(refs),
            created_follow_up_objective_id=created_follow_up_id,
            updated_objective_id=updated_objective_id,
            final_suggested_next_action=_next_action(review),
        )

    async def _handle_portfolio_after_review(
        self,
        mandate: str,
        target: ResidentReviewTarget,
        review: ResidentArtifactReview,
        review_ref: str,
    ) -> tuple[str, ...]:
        objectives = await self._backend.list_objectives(mandate)
        by_id = {item.id: item for item in objectives}
        refs: list[str] = []
        if (
            review.decision == ResidentReviewDecision.FAILED.value
            and self._config.max_follow_up_objectives > 0
        ):
            follow_up = _follow_up_objective(mandate, target, review, review_ref)
            refs.append(await self._backend.write_objective(follow_up))
            by_id[follow_up.id] = follow_up
            source = by_id.get(target.source_objective_id)
            if source is not None:
                updated = source.with_updates(
                    status=ResidentObjectiveStatus.PAUSED.value,
                    proof_progress=_merge_text(
                        source.proof_progress,
                        (f"review failed: {review_ref}",),
                    ),
                    artifact_links=_merge_text(source.artifact_links, (review_ref,)),
                    last_reviewed_at=datetime.now(UTC),
                )
                refs.append(await self._backend.write_objective(updated))
                by_id[updated.id] = updated
        else:
            source = by_id.get(target.source_objective_id)
            if source is not None:
                status = (
                    ResidentObjectiveStatus.COMPLETED.value
                    if target.complete_objective_on_pass
                    else source.status
                )
                updated = source.with_updates(
                    status=status,
                    proof_progress=_merge_text(
                        source.proof_progress,
                        (f"review passed: {review_ref}",),
                    ),
                    artifact_links=_merge_text(
                        source.artifact_links,
                        (target.artifact_ref, review_ref),
                    ),
                    last_reviewed_at=datetime.now(UTC),
                )
                refs.append(await self._backend.write_objective(updated))
                by_id[updated.id] = updated

        portfolio = await self._backend.read_portfolio(mandate)
        if portfolio is None:
            portfolio = ResidentPortfolio(mandate=mandate)
        portfolio_ref = await self._backend.write_portfolio(
            portfolio.with_objectives(tuple(by_id.values()))
        )
        refs.append(portfolio_ref)
        return tuple(refs)


def _follow_up_objective(
    mandate: str,
    target: ResidentReviewTarget,
    review: ResidentArtifactReview,
    review_ref: str,
) -> ResidentObjective:
    failed = tuple(item for item in review.evidence if item.status != "passed")
    title = f"Fix review failures for {target.title}"
    return ResidentObjective(
        id=review.follow_up_objective_id or _follow_up_objective_id(target, review),
        title=title,
        purpose=f"Correct verification failures for {target.artifact_ref}.",
        serves_mandate_because=(
            "The resident must not treat unverified work as complete for this mandate."
        ),
        expected_outcome="The artifact passes its resident review checks.",
        proof_criteria=tuple(item.description for item in failed)
        or ("Review is rerun and passes.",),
        kind=ResidentObjectiveKind.VERIFICATION.value,
        dependencies=(target.source_objective_id,) if target.source_objective_id else (),
        required_capabilities=("resident_review",),
        status=ResidentObjectiveStatus.CANDIDATE.value,
        source_evidence=(review_ref, *tuple(item.summary for item in failed)),
        reasoning=f"Created from failed resident review for mandate: {mandate[:160]}",
        artifact_links=(target.artifact_ref, review_ref),
    )


def _follow_up_objective_id(
    target: ResidentReviewTarget,
    review: ResidentArtifactReview,
) -> str:
    return _slug(f"review-follow-up-{target.id}-{review.id}")


def _review_reason(decision: str, evidence: tuple[ResidentVerificationEvidence, ...]) -> str:
    if decision == ResidentReviewDecision.PASSED.value:
        return f"{len(evidence)} verification check(s) passed with concrete evidence."
    failed = [item.summary for item in evidence if item.status != "passed"]
    return "Failed verification checks: " + "; ".join(failed)


def _next_action(review: ResidentArtifactReview) -> str:
    if review.decision == ResidentReviewDecision.PASSED.value:
        return "Use the reviewed artifact as trusted evidence for the resident portfolio."
    if review.decision == ResidentReviewDecision.SKIPPED_DUPLICATE.value:
        return "Reuse the prior review evidence instead of rerunning duplicate checks."
    return "Advance the generated follow-up objective and rerun review after correction."


def _review_key(target: ResidentReviewTarget) -> str:
    if target.review_key.strip():
        return target.review_key.strip()
    commands = "|".join(" ".join(check.command) for check in target.checks)
    return f"{target.artifact_ref}:{commands}"


def _review_id(target: ResidentReviewTarget, *, prefix: str = "review") -> str:
    return _slug(f"{prefix}-{target.id}-{_stamp(datetime.now(UTC))}")


def _render_review(review: ResidentArtifactReview) -> str:
    evidence = "\n\n".join(_render_evidence(item) for item in review.evidence) or "none"
    target = review.target
    return (
        f"# Resident Artifact Review: {target.title}\n\n"
        f"- id: {review.id}\n"
        f"- review_key: {review.review_key}\n"
        f"- target_id: {target.id}\n"
        f"- artifact_ref: {target.artifact_ref}\n"
        f"- artifact_kind: {target.artifact_kind}\n"
        f"- source_objective_id: {target.source_objective_id}\n"
        f"- decision: {review.decision}\n"
        f"- duplicate_of: {review.duplicate_of}\n"
        f"- follow_up_objective_id: {review.follow_up_objective_id}\n"
        f"- created_at: {review.created_at.isoformat()}\n\n"
        f"## Reason\n\n{review.reason}\n\n"
        f"## Target Evidence\n\n{_render_list(target.evidence)}\n\n"
        f"## Verification Evidence\n\n{evidence}\n"
    )


def _render_evidence(evidence: ResidentVerificationEvidence) -> str:
    command = " ".join(evidence.command)
    return (
        f"### {evidence.check_id}\n\n"
        f"- description: {evidence.description}\n"
        f"- status: {evidence.status}\n"
        f"- exit_code: {evidence.exit_code}\n"
        f"- command: `{command}`\n"
        f"- summary: {evidence.summary}\n\n"
        f"#### stdout\n\n```text\n{evidence.stdout[:2000]}\n```\n\n"
        f"#### stderr\n\n```text\n{evidence.stderr[:2000]}\n```\n"
    )


def _render_audit(title: str, body: str, *, refs: tuple[str, ...]) -> str:
    return (
        f"# {title}\n\n"
        f"- created_at: {datetime.now(UTC).isoformat()}\n\n"
        f"{body}\n\n"
        f"## Refs\n\n{_render_list(refs)}\n"
    )


def _parse_review(content: str) -> ResidentArtifactReview | None:
    metadata = _metadata(content)
    review_id = metadata.get("id", "")
    review_key = metadata.get("review_key", "")
    target_id = metadata.get("target_id", "")
    title = _title(content).replace("Resident Artifact Review:", "").strip()
    if not review_id or not review_key or not target_id:
        return None
    target = ResidentReviewTarget(
        id=target_id,
        title=title or target_id,
        artifact_ref=metadata.get("artifact_ref", ""),
        artifact_kind=metadata.get("artifact_kind", ""),
        source_objective_id=metadata.get("source_objective_id", ""),
        checks=(),
        evidence=tuple(_section_items(content, "Target Evidence")),
    )
    return ResidentArtifactReview(
        id=review_id,
        review_key=review_key,
        target=target,
        decision=metadata.get("decision", ""),
        reason=_section(content, "Reason"),
        duplicate_of=metadata.get("duplicate_of", ""),
        follow_up_objective_id=metadata.get("follow_up_objective_id", ""),
    )


def _metadata(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in content.splitlines():
        match = re.match(r"^\s*-\s*([a-zA-Z0-9_]+):\s*(.*?)\s*$", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def _title(content: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", content, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _section(content: str, heading: str) -> str:
    pattern = rf"^##\s+{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^##\s+|\Z)"
    match = re.search(pattern, content, flags=re.MULTILINE | re.DOTALL)
    return match.group("body").strip() if match else ""


def _section_items(content: str, heading: str) -> list[str]:
    body = _section(content, heading)
    return [
        match.group(1).strip()
        for match in re.finditer(r"^\s*-\s+(.+?)\s*$", body, flags=re.MULTILINE)
    ]


def _render_list(items: tuple[str, ...]) -> str:
    return "\n".join(f"- {item}" for item in items if item) or "- none"


def _merge_text(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*left, *right)))


def _slug(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value))
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:96] or "item"


def _stamp(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S%fZ")
