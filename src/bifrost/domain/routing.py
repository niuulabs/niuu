"""Routing domain logic: rule evaluation before strategy selection.

``apply_rules`` is the single entry-point called by the ``ModelRouter`` before
it builds provider candidates.  It delegates to whichever ``RuleEnginePort``
implementation is configured, and handles five possible outcomes:

* ``route_to``     — returns a copy of the request with the model overridden.
* ``reject``       — raises ``RuleRejectError`` (callers map this to HTTP 400).
* ``log``          — emits a full-level audit log entry, returns the request unchanged.
* ``tag``          — emits an audit log entry with metadata tags, returns the request unchanged.
* ``strip_images`` — returns a copy of the request with all image blocks removed.
"""

from __future__ import annotations

import logging

import bifrost.metrics as _metrics
from bifrost.ports.rules import RoutingContext, RuleAction, RuleEnginePort
from bifrost.translation.models import AnthropicRequest, ImageBlock, TextBlock

logger = logging.getLogger(__name__)


def _strip_image_blocks(request: AnthropicRequest) -> AnthropicRequest:
    """Return a copy of *request* with all image blocks removed from messages.

    If stripping images would leave a message with no content blocks, a
    placeholder ``TextBlock(text="[image removed]")`` is inserted so that
    downstream providers never receive an empty content list.
    """
    new_messages = []
    for msg in request.messages:
        if isinstance(msg.content, str):
            new_messages.append(msg)
            continue
        filtered = [b for b in msg.content if not isinstance(b, ImageBlock)]
        if not filtered:
            filtered = [TextBlock(text="[image removed]")]
        new_messages.append(msg.model_copy(update={"content": filtered}))
    return request.model_copy(update={"messages": new_messages})


class RuleRejectError(Exception):
    """Raised when a routing rule rejects the request."""

    def __init__(self, rule_name: str, message: str) -> None:
        super().__init__(message)
        self.rule_name = rule_name
        self.message = message


def apply_rules(
    request: AnthropicRequest,
    context: RoutingContext,
    engine: RuleEnginePort | None,
) -> AnthropicRequest:
    """Evaluate *engine*'s rules against *request* and act on the first match.

    If *engine* is ``None`` (no rules configured), the request is returned
    unchanged.

    Args:
        request: Inbound Anthropic-format request.
        context: Per-request routing context (agent budget, etc.).
        engine:  Rule engine to evaluate, or ``None`` to skip.

    Returns:
        The original request, or a copy with an overridden model when a
        ``route_to`` rule fires.

    Raises:
        RuleRejectError: When a ``reject`` rule fires.
    """
    if engine is None:
        return request

    match_result = engine.evaluate(request, context)
    if match_result is None:
        return request

    _metrics.record_rule_hit(
        rule_name=match_result.rule_name,
        action=match_result.action.value,
    )

    match match_result.action:
        case RuleAction.ROUTE_TO:
            logger.debug(
                "Rule '%s' rerouting model '%s' → '%s'",
                match_result.rule_name,
                request.model,
                match_result.target,
            )
            return request.model_copy(update={"model": match_result.target})

        case RuleAction.REJECT:
            reject_msg = match_result.message or (
                f"Request rejected by rule '{match_result.rule_name}'"
            )
            logger.info(
                "Rule '%s' rejected request for model '%s': %s",
                match_result.rule_name,
                request.model,
                reject_msg,
            )
            raise RuleRejectError(match_result.rule_name, reject_msg)

        case RuleAction.LOG:
            logger.info(
                "AUDIT rule='%s' model='%s' action=log",
                match_result.rule_name,
                request.model,
            )
            return request

        case RuleAction.TAG:
            logger.info(
                "AUDIT rule='%s' model='%s' action=tag tags=%s",
                match_result.rule_name,
                request.model,
                match_result.tags,
            )
            return request

        case RuleAction.STRIP_IMAGES:
            logger.info(
                "Rule '%s' stripping image blocks from request for model '%s'",
                match_result.rule_name,
                request.model,
            )
            return _strip_image_blocks(request)

        case _:
            logger.warning("Unknown rule action '%s'; ignoring", match_result.action)
            return request
    raise AssertionError("Unreachable apply_rules fallthrough")
