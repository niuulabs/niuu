"""HTTP room controls and route composition for resident Valkyries."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ravn.api.valkyrie_learning_projection import (
    _decision_request_for_learning,
    _learning_edits,
    _learning_feedback_action,
    _raw_learning_id,
)
from ravn.api.valkyrie_projection import Dashboard, ValkyrieDashboardProjection
from ravn.api.valkyrie_projection_common import _as_float, _as_string_list, _now
from ravn.api.valkyrie_requests import (
    LEARNING_FEEDBACK_VERDICTS,
    AutonomyUpdateRequest,
    HuddleJoinRequest,
    HuddleSendRequest,
    LearningDecisionRequest,
    LearningFeedbackRequest,
    LearningReviseRequest,
)
from ravn.api.valkyrie_runtime_projection import (
    _huddle_role_for_action,
    _resolve_huddle_message_author,
    _validate_huddle_join_scope,
)
from ravn.config import Settings, ValkyrieRoomConfig
from ravn.odin.review import ReviewItem, ReviewKind, review_decided_event
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher

logger = logging.getLogger(__name__)


class ValkyrieRoomClient:
    """Call Skuld's real room endpoints for dashboard huddle operations."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def require_capability(self, participant_id: str, capability: str) -> None:
        """Raise 403 unless the joined participant holds *capability*."""
        await self._post(
            "/api/room/require-capability",
            {"participant_id": participant_id, "capability": capability},
        )

    async def join_huddle(
        self,
        huddle: dict[str, Any],
        request: HuddleJoinRequest,
    ) -> dict[str, Any]:
        environment_id = str(huddle.get("environmentId") or huddle.get("environment_id") or "")
        if not environment_id:
            raise HTTPException(status_code=422, detail="Huddle has no environment id")
        participant_id = request.participantId.strip()
        if not participant_id:
            raise HTTPException(status_code=422, detail="participantId is required")
        payload = {
            "participant_id": participant_id,
            "display_name": request.displayName or participant_id,
            "environment_id": environment_id,
            "role": _huddle_role_for_action(request.action),
            "room_id": str(huddle.get("id") or ""),
            "capabilities": request.capabilities,
            "surfaces": ["skuld.room", "ravn.valkyrie.dashboard"],
        }
        authorities = _as_string_list(
            huddle.get("environmentActionAuthorities")
            or huddle.get("environment_action_authorities")
            or []
        )
        if authorities:
            payload["environment_action_authorities"] = authorities
        return await self._post("/api/room/join", payload)

    async def leave_huddle(self, huddle: dict[str, Any]) -> dict[str, Any]:
        participant_id = str(huddle.get("joinedParticipantId") or "").strip()
        if not participant_id:
            raise HTTPException(status_code=422, detail="Huddle has no joined participant id")
        return await self._post(
            "/api/room/leave",
            {"participant_id": participant_id, "reason": f"left {huddle.get('id') or ''}"},
        )

    async def send_huddle_message(
        self,
        huddle: dict[str, Any],
        request: HuddleSendRequest,
    ) -> dict[str, Any]:
        participant_id, _ = _resolve_huddle_message_author(huddle, request)
        metadata = {
            "room_id": str(huddle.get("id") or request.huddleId),
            "huddle_id": request.huddleId,
            "environment_id": str(huddle.get("environmentId") or ""),
        }
        action = str(huddle.get("joinedAction") or "").strip()
        if action:
            metadata["action"] = action
        target_flock_id = str(
            huddle.get("targetFlockId") or huddle.get("target_flock_id") or ""
        ).strip()
        if target_flock_id:
            metadata["target_flock_id"] = target_flock_id
        directed_to = [target for target in request.directedTo if str(target).strip()]
        if directed_to:
            result = None
            for target in directed_to:
                result = await self._post(
                    "/api/room/direct",
                    {
                        "target_peer_id": target,
                        "content": request.body,
                        "source": "ravn.valkyrie.dashboard",
                        "participant_id": participant_id,
                        "metadata": metadata,
                    },
                )
            return result or {"status": "sent"}
        return await self._post(
            "/api/room/message",
            {
                "content": request.body,
                "source": "ravn.valkyrie.dashboard",
                "participant_id": participant_id,
                "metadata": metadata,
            },
        )

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
            ) as client:
                response = await client.post(path, json=payload)
                response.raise_for_status()
                data = response.json()
                return data if isinstance(data, dict) else {"status": "ok", "data": data}
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=exc.response.text[:500],
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Skuld room request failed: {exc}",
            ) from exc


def build_skuld_room_client_from_env(
    config: ValkyrieRoomConfig | None = None,
) -> ValkyrieRoomClient | None:
    """Build the optional room bridge from validated Ravn settings."""
    loaded = config or Settings().valkyrie.room
    if not loaded.url.strip():
        return None
    return ValkyrieRoomClient(loaded.url, timeout_seconds=loaded.timeout_seconds)


class OdinReviewCommandPublisher:
    """Publish operator review decisions onto the existing Sleipnir bus.

    Every operator intervention — learning verdicts, autonomy changes,
    inbox approvals — rides the same ``odin.review.decided`` envelope.
    """

    def __init__(
        self,
        publisher: SleipnirPublisher | None = None,
        *,
        source: str = "ravn:odin-review",
        start_timeout_seconds: float = 5.0,
    ) -> None:
        self._publisher = publisher
        self._source = source
        self._start_timeout_seconds = max(start_timeout_seconds, 1.0)

    async def start(self) -> None:
        if self._publisher is not None and hasattr(self._publisher, "start"):
            await asyncio.wait_for(
                self._publisher.start(),  # type: ignore[attr-defined]
                timeout=self._start_timeout_seconds,
            )

    async def stop(self) -> None:
        if self._publisher is not None and hasattr(self._publisher, "stop"):
            await self._publisher.stop()  # type: ignore[attr-defined]

    async def publish_review_decision(
        self,
        item: ReviewItem,
    ) -> tuple[dict[str, Any], SleipnirEvent | None]:
        return await self.publish_event(review_decided_event(item, source=self._source))

    async def publish_event(
        self,
        event: SleipnirEvent,
    ) -> tuple[dict[str, Any], SleipnirEvent | None]:
        if self._publisher is None:
            return (
                {
                    "published": False,
                    "eventType": event.event_type,
                    "eventId": event.event_id,
                    "message": (
                        "No Sleipnir publisher configured; recorded in dashboard projection only."
                    ),
                    "observedAt": _now(),
                },
                None,
            )
        if hasattr(self._publisher, "publish_with_results"):
            targets = await self._publisher.publish_with_results(event)  # type: ignore[attr-defined]
        else:
            await self._publisher.publish(event)
            targets = [
                {
                    "label": "default",
                    "published": True,
                    "message": "published",
                }
            ]
        failed_targets = [target for target in targets if not bool(target.get("published"))]
        published_count = len(targets) - len(failed_targets)
        return (
            {
                "published": bool(targets) and not failed_targets,
                "eventType": event.event_type,
                "eventId": event.event_id,
                "message": (
                    "Published to all configured resident Valkyrie command targets."
                    if targets and not failed_targets
                    else (
                        f"Published to {published_count}/{len(targets)} configured "
                        "resident Valkyrie command targets."
                    )
                ),
                "targetCount": len(targets),
                "publishedTargets": published_count,
                "failedTargets": len(failed_targets),
                "targets": targets,
                "observedAt": _now(),
            },
            event,
        )


def _review_item_for_learning_action(
    action: str,
    before: dict[str, Any],
    learning: dict[str, Any],
    request: LearningDecisionRequest,
    *,
    operator_id: str,
    feedback: dict[str, Any] | None = None,
    revision: dict[str, Any] | None = None,
) -> ReviewItem:
    """Project a dashboard learning action onto the unified review envelope.

    The action vocabulary maps to one of two kinds: scope changes are
    ``skill_promotion`` items applied by the learning's source resident;
    everything else is a ``flock_learning`` item broadcast to the flock and
    relevance-filtered by each resident. Operator feedback and revision
    payloads travel in the evidence so residents can record them locally.
    """
    raw_learning_id = _raw_learning_id(str(learning.get("id") or request.learningId))
    source_environment_id = str(
        learning.get("sourceEnvironmentId") or before.get("sourceEnvironmentId") or ""
    )
    title = str(learning.get("promotedTool") or learning.get("title") or raw_learning_id)
    summary = request.reason or str(learning.get("summary") or title)
    correlation_id = f"valkyrie-learning:{raw_learning_id}:{action}"

    if action in {"promote", "demote"}:
        from_scope = str(before.get("scope") or before.get("currentScope") or "private")
        to_scope = str(
            request.targetScope
            or learning.get("scope")
            or learning.get("currentScope")
            or "private"
        )
        item = ReviewItem.new(
            kind=ReviewKind.SKILL_PROMOTION.value,
            requested_action=action,
            environment_id=source_environment_id or "unknown",
            valkyrie_id=str(learning.get("sourceValkyrieId") or ""),
            title=title,
            summary=summary,
            audience="environment" if not learning.get("sourceValkyrieId") else "valkyrie",
            domain=str(learning.get("domain") or learning.get("domainScope") or ""),
            evidence={
                "skill_name": title,
                "learning_id": raw_learning_id,
                "from_scope": from_scope,
                "to_scope": to_scope,
                "confidence": _as_float(learning.get("confidence"), 0.0),
                **({"feedback": feedback} if feedback else {}),
            },
            requested_by=operator_id,
            correlation_id=correlation_id,
        )
        item.decide(decision="approved", operator_id=operator_id, reason=request.reason)
        return item

    requested_action = {
        "adopt": "adopt",
        "override": "adopt",
        "reject": "adopt",
        "canary": "canary",
        "rollback": "retract",
    }.get(action, action)
    canary_environment = str(request.canaryEnvironmentId or "")
    audience = "environment" if (action == "canary" and canary_environment) else "flock"
    item = ReviewItem.new(
        kind=ReviewKind.FLOCK_LEARNING.value,
        requested_action=requested_action,
        environment_id=(
            canary_environment if audience == "environment" else source_environment_id or "unknown"
        ),
        valkyrie_id="",
        title=title,
        summary=summary,
        audience=audience,
        flock_id=str(learning.get("targetFlockId") or ""),
        domain=str(learning.get("domain") or learning.get("domainScope") or ""),
        evidence={
            "artifact": {
                "learning_id": raw_learning_id,
                "title": title,
                "summary": str(learning.get("summary") or title),
                "content": str(learning.get("artifactContent") or ""),
                "artifact_type": str(learning.get("artifactType") or "ravn_skill_tool"),
                "scope": str(request.targetScope or learning.get("scope") or "flock"),
                "confidence": _as_float(learning.get("confidence"), 0.0),
                "source_environment_id": source_environment_id,
                "source_valkyrie_id": str(learning.get("sourceValkyrieId") or ""),
                "promotion_id": raw_learning_id,
                "flock_id": str(learning.get("targetFlockId") or ""),
                "domain": str(learning.get("domain") or learning.get("domainScope") or ""),
                "redaction_status": str(learning.get("redaction") or "redacted"),
                "artifact_path": str(learning.get("artifactPath") or ""),
                "supersedes": _raw_learning_id(str(learning.get("supersedes") or "")),
            },
            "ui_learning_id": str(learning.get("id") or request.learningId),
            "status_before": str(before.get("status") or ""),
            **({"feedback": feedback} if feedback else {}),
            **({"revision": revision} if revision else {}),
        },
        requested_by=operator_id,
        correlation_id=correlation_id,
    )
    item.decide(
        decision="rejected" if action == "reject" else "approved",
        operator_id=operator_id,
        reason=request.reason,
    )
    return item


class _CommandTarget:
    label: str
    publisher: SleipnirPublisher


class _FanoutSleipnirPublisher:
    """Publish one command event to every configured resident command target."""

    def __init__(self, targets: list[_CommandTarget]) -> None:
        self._targets = targets

    async def start(self) -> None:
        await asyncio.gather(
            *(
                target.publisher.start()  # type: ignore[attr-defined]
                for target in self._targets
                if hasattr(target.publisher, "start")
            )
        )

    async def stop(self) -> None:
        await asyncio.gather(
            *(
                target.publisher.stop()  # type: ignore[attr-defined]
                for target in self._targets
                if hasattr(target.publisher, "stop")
            ),
            return_exceptions=True,
        )

    async def publish(self, event: SleipnirEvent) -> None:
        results = await self.publish_with_results(event)
        failures = [result for result in results if not bool(result["published"])]
        if failures:
            labels = ", ".join(str(result["label"]) for result in failures)
            raise RuntimeError(f"failed to publish to command targets: {labels}")

    async def publish_with_results(self, event: SleipnirEvent) -> list[dict[str, Any]]:
        results = await asyncio.gather(
            *(self._publish_one(target, event) for target in self._targets),
        )
        return list(results)

    async def _publish_one(
        self,
        target: _CommandTarget,
        event: SleipnirEvent,
    ) -> dict[str, Any]:
        try:
            await target.publisher.publish(event)
        except Exception as exc:  # noqa: BLE001
            return {
                "label": target.label,
                "published": False,
                "message": str(exc),
            }
        return {
            "label": target.label,
            "published": True,
            "message": "published",
        }


def create_valkyrie_router(
    projection: ValkyrieDashboardProjection | None = None,
    review_command_publisher: OdinReviewCommandPublisher | None = None,
    room_client: ValkyrieRoomClient | None = None,
    review_service: Any | None = None,
    history_service: Any | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/ravn/valkyrie", tags=["Ravn Valkyries"])
    store = projection or ValkyrieDashboardProjection()
    command_publisher = review_command_publisher or OdinReviewCommandPublisher()
    skuld_room = room_client or build_skuld_room_client_from_env()

    def _require_history() -> Any:
        if history_service is None:
            raise HTTPException(
                status_code=503,
                detail="Valkyrie history store is not configured on this API process",
            )
        return history_service

    async def _record_in_review_ledger(item: ReviewItem) -> None:
        """Operator-initiated decisions land in the same central ledger."""
        if review_service is None:
            return
        try:
            await review_service.record_decided(item)
        except Exception:
            logger.exception("valkyrie controls: review ledger record failed")

    async def finish_learning_action(
        action: str,
        before: dict[str, Any],
        learning: dict[str, Any],
        request: LearningDecisionRequest,
        *,
        feedback: dict[str, Any] | None = None,
        revision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = _review_item_for_learning_action(
            action,
            before,
            learning,
            request,
            operator_id=request.operatorId,
            feedback=feedback,
            revision=revision,
        )
        try:
            delivery, event = await command_publisher.publish_review_decision(item)
        except Exception as exc:  # noqa: BLE001
            logger.warning("valkyrie review command publish failed: %s", exc)
            delivery = {
                "published": False,
                "eventType": "",
                "eventId": "",
                "message": f"Sleipnir/NATS publish failed: {exc}",
                "observedAt": _now(),
            }
            event = None
        if event is not None:
            store.record_event(event)
        await _record_in_review_ledger(item)
        return store.record_learning_command_delivery(str(learning["id"]), delivery)

    @router.get("/dashboard")
    async def get_dashboard() -> Dashboard:
        return store.dashboard()

    @router.get("/environments")
    async def list_environments() -> list[dict[str, Any]]:
        return store.environments()

    @router.get("/environments/{environment_id}")
    async def get_environment(environment_id: str) -> dict[str, Any]:
        return store.environment(environment_id)

    @router.get("/flocks")
    async def list_flocks() -> list[dict[str, Any]]:
        return store.flocks()

    @router.get("/flocks/{flock_id}")
    async def get_flock(flock_id: str) -> dict[str, Any]:
        return store.flock(flock_id)

    @router.post("/huddles/{huddle_id}/join")
    async def join_huddle(huddle_id: str, request: HuddleJoinRequest) -> dict[str, Any]:
        if request.huddleId != huddle_id:
            raise HTTPException(status_code=422, detail="Huddle id mismatch")
        huddle = store.huddle_for_room(huddle_id)
        _validate_huddle_join_scope(huddle, request)
        if skuld_room is None:
            raise HTTPException(status_code=503, detail="Skuld room bridge is not configured")
        if skuld_room is not None:
            await skuld_room.join_huddle(huddle, request)
        return store.join_huddle(request)

    @router.post("/huddles/{huddle_id}/leave")
    async def leave_huddle(huddle_id: str) -> dict[str, Any]:
        if skuld_room is not None:
            await skuld_room.leave_huddle(store.huddle_for_room(huddle_id))
        return store.leave_huddle(huddle_id)

    @router.post("/huddles/{huddle_id}/messages")
    async def send_huddle_message(
        huddle_id: str,
        request: HuddleSendRequest,
    ) -> dict[str, Any]:
        if request.huddleId != huddle_id:
            raise HTTPException(status_code=422, detail="Huddle id mismatch")
        huddle = store.huddle_for_room(huddle_id)
        _resolve_huddle_message_author(huddle, request)
        directed_to = [target for target in request.directedTo if str(target).strip()]
        if directed_to and skuld_room is None:
            # A directed message that never leaves this projection would be a
            # silent failure — refuse instead of pretending it was delivered.
            raise HTTPException(
                status_code=503,
                detail=(
                    "Skuld room bridge is not configured; the direct message "
                    "cannot reach the resident"
                ),
            )
        if skuld_room is not None:
            await skuld_room.send_huddle_message(huddle, request)
        return store.send_huddle_message(request)

    @router.get("/learnings/{learning_id}")
    async def get_learning(learning_id: str) -> dict[str, Any]:
        return store.learning(learning_id)

    @router.post("/learnings/{learning_id}/adopt")
    async def adopt_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        learning = store.decide_learning(learning_id, "adopted", request, action="adopt")
        return await finish_learning_action("adopt", before, learning, request)

    @router.post("/learnings/{learning_id}/reject")
    async def reject_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        learning = store.decide_learning(learning_id, "rejected", request, action="reject")
        return await finish_learning_action("reject", before, learning, request)

    @router.post("/learnings/{learning_id}/override")
    async def override_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        learning = store.decide_learning(learning_id, "adopted", request, action="override")
        return await finish_learning_action("override", before, learning, request)

    @router.post("/learnings/{learning_id}/canary")
    async def canary_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        learning = store.canary_learning(learning_id, request)
        return await finish_learning_action("canary", before, learning, request)

    @router.post("/learnings/{learning_id}/promote")
    async def promote_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        learning = store.promote_learning(learning_id, request)
        return await finish_learning_action("promote", before, learning, request)

    @router.post("/learnings/{learning_id}/demote")
    async def demote_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        learning = store.demote_learning(learning_id, request)
        return await finish_learning_action("demote", before, learning, request)

    @router.post("/learnings/{learning_id}/rollback")
    async def rollback_learning(
        learning_id: str,
        request: LearningDecisionRequest,
    ) -> dict[str, Any]:
        if request.learningId != learning_id:
            raise HTTPException(status_code=422, detail="Learning id mismatch")
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        learning = store.rollback_learning(learning_id, request)
        return await finish_learning_action("rollback", before, learning, request)

    @router.post("/learnings/{learning_id}/feedback")
    async def learning_feedback(
        learning_id: str,
        request: LearningFeedbackRequest,
    ) -> dict[str, Any]:
        if request.verdict not in LEARNING_FEEDBACK_VERDICTS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown feedback verdict {request.verdict!r}; "
                    f"expected one of {', '.join(LEARNING_FEEDBACK_VERDICTS)}"
                ),
            )
        if request.verdict == "wrong_tier" and not request.targetScope:
            raise HTTPException(
                status_code=422,
                detail="wrong_tier feedback requires targetScope",
            )
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        decision_request = _decision_request_for_learning(learning_id, request)
        action = _learning_feedback_action(
            request.verdict,
            str(before.get("status") or ""),
            current_scope=str(before.get("scope") or before.get("currentScope") or "private"),
            target_scope=request.targetScope,
        )
        # Lifecycle first: adjacency violations must 422 before feedback lands.
        if action == "rollback":
            store.rollback_learning(learning_id, decision_request)
        elif action == "reject":
            store.decide_learning(learning_id, "rejected", decision_request, action="reject")
        elif action == "promote":
            store.promote_learning(learning_id, decision_request)
        elif action == "demote":
            store.demote_learning(learning_id, decision_request)
        learning = store.record_learning_feedback(learning_id, request)
        feedback = dict(learning.get("feedback") or {})
        return await finish_learning_action(
            action,
            before,
            learning,
            decision_request,
            feedback=feedback,
        )

    @router.post("/learnings/{learning_id}/revise")
    async def revise_learning(
        learning_id: str,
        request: LearningReviseRequest,
    ) -> dict[str, Any]:
        edits = _learning_edits(request)
        if not edits:
            raise HTTPException(
                status_code=422,
                detail="Revision needs at least one of title, summary, or content",
            )
        await _require_operator_capability(request.operatorId, "approve")
        before = store.learning(learning_id)
        supersede = str(before.get("status") or "") in {"adopted", "canary"}
        if supersede:
            learning = store.revise_learning_supersede(learning_id, request)
            superseded_id = learning_id
        else:
            learning = store.revise_learning_in_place(learning_id, request)
            superseded_id = ""
        decision_request = _decision_request_for_learning(str(learning["id"]), request)
        revision = {
            "title": request.title,
            "summary": request.summary,
            "content": request.content,
            "reason": request.reason,
            "revision_id": _raw_learning_id(str(learning["id"])),
            "superseded_id": _raw_learning_id(superseded_id) if superseded_id else "",
        }
        updated = await finish_learning_action(
            "revise",
            before,
            learning,
            decision_request,
            revision=revision,
        )
        return {"learning": updated, "supersededId": superseded_id}

    @router.post("/proof/replay-signal")
    async def replay_signal(signal: dict[str, Any]) -> dict[str, Any]:
        return store.replay_signal(signal)

    async def _require_operator_capability(participant_id: str, capability: str) -> None:
        """Enforce room capabilities on operator control endpoints.

        When Skuld rooms are configured the participant must hold the
        capability (403 otherwise). Without a room client (local dev,
        proofs) controls stay open but the gap is logged loudly.
        """
        if skuld_room is None:
            logger.warning(
                "valkyrie controls: no Skuld room client configured; "
                "operator capabilities are not enforced",
            )
            return
        if not participant_id:
            raise HTTPException(
                status_code=403,
                detail=f"participantId with the {capability!r} capability is required",
            )
        await skuld_room.require_capability(participant_id, capability)

    @router.post("/autonomy")
    async def update_autonomy(request: AutonomyUpdateRequest) -> Dashboard:
        await _require_operator_capability(request.participantId, "change_autonomy")
        before = store.dashboard()
        previous_mode = next(
            (
                str(entry.get("autonomyMode") or "")
                for entry in before.get("valkyries", [])
                if entry.get("id") == request.valkyrieId
            ),
            "",
        )
        dashboard = store.update_autonomy(request)
        valkyrie = next(
            (
                entry
                for entry in dashboard.get("valkyries", [])
                if entry.get("id") == request.valkyrieId
            ),
            {"id": request.valkyrieId},
        )
        operator_id = request.participantId or "operator"
        item = ReviewItem.new(
            kind=ReviewKind.AUTONOMY_CHANGE.value,
            requested_action="set_autonomy_mode",
            environment_id=str(valkyrie.get("environmentId") or ""),
            valkyrie_id=str(valkyrie.get("id") or request.valkyrieId),
            title=f"Set {request.valkyrieId} autonomy to {request.mode}",
            summary=request.reason or f"Operator set autonomy to {request.mode}",
            urgency=0.4,
            evidence={"mode": request.mode, "previous_mode": previous_mode},
            requested_by=operator_id,
            correlation_id=f"valkyrie-autonomy:{request.valkyrieId}",
        )
        item.decide(decision="approved", operator_id=operator_id, reason=request.reason)
        try:
            _delivery, event = await command_publisher.publish_review_decision(item)
        except Exception as exc:  # noqa: BLE001
            logger.warning("valkyrie autonomy command publish failed: %s", exc)
            event = None
        if event is not None:
            store.record_event(event)
        await _record_in_review_ledger(item)
        return store.dashboard()

    @router.get("/decisions")
    async def list_decisions(
        environment_id: str = "",
        valkyrie_id: str = "",
        operational_state: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        history = _require_history()
        rows, total = await history.store.list_decisions(
            environment_id=environment_id,
            valkyrie_id=valkyrie_id,
            operational_state=operational_state,
            limit=limit,
            offset=offset,
        )
        return {"items": rows, "total": total, "limit": limit, "offset": offset}

    @router.get("/decisions/{decision_id}")
    async def get_decision(decision_id: str) -> dict[str, Any]:
        history = _require_history()
        detail = await history.decision_detail(decision_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Decision not found")
        return detail

    @router.get("/signals/history")
    async def list_signal_history(
        environment_id: str = "",
        severity: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        history = _require_history()
        rows, total = await history.store.list_signals(
            environment_id=environment_id,
            severity=severity,
            limit=limit,
            offset=offset,
        )
        return {"items": rows, "total": total, "limit": limit, "offset": offset}

    @router.get("/learnings/stats/skills")
    async def learning_skill_stats(environment_id: str = "") -> dict[str, Any]:
        history = _require_history()
        skills = await history.skill_stats(environment_id=environment_id)
        return {"skills": skills}

    @router.get("/telemetry/events")
    async def list_telemetry_events(
        limit: int = 200,
        event_type: str = "",
        environment_id: str = "",
        valkyrie_id: str = "",
        contains: str = "",
    ) -> list[dict[str, Any]]:
        return store.telemetry_events(
            limit=limit,
            event_type=event_type,
            environment_id=environment_id,
            valkyrie_id=valkyrie_id,
            contains=contains,
        )

    @router.post("/telemetry/events")
    async def record_telemetry_event(event: dict[str, Any], minimal: bool = False) -> Dashboard:
        store.record_event(event)
        if history_service is not None:
            try:
                await history_service.ingest_event(event)
            except Exception:
                logger.exception("valkyrie telemetry: history ingest failed")
        if review_service is not None:
            try:
                await review_service.ingest_event(event)
            except Exception:
                logger.exception("valkyrie telemetry: review queue ingest failed")
        if minimal:
            return {
                "accepted": True,
                "eventType": str(event.get("event_type") or event.get("eventType") or ""),
                "eventId": str(event.get("event_id") or event.get("eventId") or ""),
                "observedAt": _now(),
            }
        return store.dashboard()

    @router.get("/logs")
    async def list_logs() -> list[dict[str, Any]]:
        return store.logs()

    @router.get("/signals")
    async def signal_stream(replay_once: bool = False) -> StreamingResponse:
        async def generate():
            events = store.events()
            while True:
                for event in events:
                    yield f"data: {json.dumps(event)}\n\n"
                    if not replay_once:
                        await asyncio.sleep(0.75)
                if replay_once:
                    return
                yield ": keepalive\n\n"
                await asyncio.sleep(5)

        return StreamingResponse(generate(), media_type="text/event-stream")

    return router
