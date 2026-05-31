"""YamlRuleEngine — rule engine backed by YAML-configurable rule definitions.

Rules are stored as ``RuleConfig`` objects (loaded from YAML via Pydantic).
Each rule has a ``when`` block with zero or more conditions; all conditions
must match for the rule to fire (AND semantics).  Rules are evaluated in the
order they appear in the config; first match wins.

Supported conditions
--------------------
* ``model``                 — request model equals this alias or model ID.
* ``max_tokens``            — numeric comparison expression (e.g. ``'<= 512'``).
* ``thinking``              — thinking enabled / disabled (bool).
* ``agent_budget_pct``      — numeric comparison on remaining budget % (e.g. ``'>= 80'``).
* ``provider``              — resolved primary provider name equals this value.
* ``has_tools``             — request includes tool definitions (bool).
* ``content_matches``       — regex applied to full concatenated message content.
* ``system_prompt_matches`` — regex applied to the system prompt text.
* ``message_count``         — numeric comparison on the number of messages.
* ``has_image``             — request contains image blocks (bool).
* ``agent_id``              — fnmatch pattern on the X-Ravn-Agent-Id header (e.g. ``'reviewer*'``).

Numeric comparison expressions
-------------------------------
Supported operators: ``<=``, ``>=``, ``<``, ``>``, ``==``, ``!=``.
A plain number is treated as an equality check (``== N``).
"""

from __future__ import annotations

import fnmatch
import re

from bifrost.config import BifrostConfig, RuleCondition, RuleConfig
from bifrost.ports.rules import RoutingContext, RuleAction, RuleEnginePort, RuleMatch
from bifrost.translation.models import AnthropicRequest, ImageBlock, TextBlock

# Pre-compiled pattern for numeric comparison expressions like '<= 512' or '>= 80'.
_CMP_RE = re.compile(r"^\s*(<=|>=|<|>|==|!=)\s*([0-9]+(?:\.[0-9]*)?)\s*$")


def _parse_numeric_expr(expr: str) -> tuple[str, float]:
    """Parse a comparison expression and return ``(operator, rhs)``.

    Args:
        expr: Expression string such as ``'<= 512'`` or ``'80'``.

    Returns:
        A ``(op, rhs)`` tuple where *op* is one of
        ``<=``, ``>=``, ``<``, ``>``, ``==``, ``!=``.

    Raises:
        ValueError: If the expression cannot be parsed.
    """
    m = _CMP_RE.match(expr)
    if m:
        return m.group(1), float(m.group(2))

    # Plain number → equality
    stripped = expr.strip()
    try:
        return "==", float(stripped)
    except ValueError:
        raise ValueError(f"Cannot parse numeric comparison expression: {expr!r}") from None


def _compare_numeric(value: float, expr: str) -> bool:
    """Return ``True`` if *value* satisfies the comparison *expr*.

    Args:
        value: Left-hand side of the comparison.
        expr:  Expression string (e.g. ``'<= 512'``).
    """
    op, rhs = _parse_numeric_expr(expr)
    match op:
        case "<=":
            return value <= rhs
        case ">=":
            return value >= rhs
        case "<":
            return value < rhs
        case ">":
            return value > rhs
        case "==":
            return value == rhs
        case "!=":
            return value != rhs
        case _:
            return False
    raise AssertionError("Unreachable _compare_numeric fallthrough")


def _thinking_enabled(request: AnthropicRequest) -> bool:
    """Return ``True`` if the request has extended thinking enabled."""
    if request.thinking is None:
        return False
    return request.thinking.get("type") == "enabled"


def _extract_message_text(request: AnthropicRequest) -> str:
    """Concatenate all text content from messages into a single string."""
    parts: list[str] = []
    for msg in request.messages:
        if isinstance(msg.content, str):
            parts.append(msg.content)
            continue
        for block in msg.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
    return "\n".join(parts)


def _extract_system_text(request: AnthropicRequest) -> str:
    """Return the system prompt as a plain string."""
    if request.system is None:
        return ""
    if isinstance(request.system, str):
        return request.system
    return "\n".join(block.text for block in request.system if isinstance(block, TextBlock))


def _request_has_image(request: AnthropicRequest) -> bool:
    """Return ``True`` if any message in *request* contains an image block."""
    for msg in request.messages:
        if isinstance(msg.content, str):
            continue
        for block in msg.content:
            if isinstance(block, ImageBlock):
                return True
    return False


class YamlRuleEngine(RuleEnginePort):
    """Rule engine driven by a list of ``RuleConfig`` objects.

    Typically constructed by the application factory from ``BifrostConfig.rules``.

    Args:
        rules:  Ordered list of rule definitions to evaluate.
        config: Gateway config used to resolve model aliases and providers.
    """

    def __init__(self, rules: list[RuleConfig], config: BifrostConfig) -> None:
        self._rules = rules
        self._config = config
        # Pre-compile regex patterns once at init time rather than per request.
        # Keyed by rule index so the hot-path _matches() can do a cheap lookup.
        self._content_patterns: dict[int, re.Pattern] = {}
        self._system_patterns: dict[int, re.Pattern] = {}
        for idx, rule in enumerate(rules):
            if rule.when.content_matches is not None:
                self._content_patterns[idx] = re.compile(rule.when.content_matches)
            if rule.when.system_prompt_matches is not None:
                self._system_patterns[idx] = re.compile(rule.when.system_prompt_matches)

    def evaluate(
        self,
        request: AnthropicRequest,
        context: RoutingContext,
    ) -> RuleMatch | None:
        """Evaluate each rule in order and return the first match.

        Args:
            request: Inbound Anthropic-format request.
            context: Per-request routing context.

        Returns:
            The first matching ``RuleMatch``, or ``None`` if no rule fires.
        """
        for idx, rule in enumerate(self._rules):
            if self._matches(idx, rule.when, request, context):
                return RuleMatch(
                    rule_name=rule.name,
                    action=RuleAction(rule.action),
                    target=rule.target,
                    message=rule.message,
                    tags=rule.tags,
                )
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _matches(
        self,
        idx: int,
        condition: RuleCondition,
        request: AnthropicRequest,
        context: RoutingContext,
    ) -> bool:
        """Return ``True`` only when ALL non-None conditions in *condition* match."""
        if condition.model is not None:
            if request.model != condition.model:
                return False

        if condition.thinking is not None:
            if _thinking_enabled(request) != condition.thinking:
                return False

        if condition.max_tokens is not None:
            if not _compare_numeric(request.max_tokens, condition.max_tokens):
                return False

        if condition.has_tools is not None:
            req_has_tools = bool(request.tools)
            if req_has_tools != condition.has_tools:
                return False

        if condition.agent_budget_pct is not None:
            if context.agent_budget_pct is None:
                # Budget not available in this context — skip condition.
                return False
            if not _compare_numeric(context.agent_budget_pct, condition.agent_budget_pct):
                return False

        if condition.provider is not None:
            resolved = self._config.resolve_alias(request.model)
            providers = self._config.providers_for_model(resolved)
            if condition.provider not in providers:
                return False

        if condition.content_matches is not None:
            text = _extract_message_text(request)
            if not self._content_patterns[idx].search(text):
                return False

        if condition.system_prompt_matches is not None:
            system_text = _extract_system_text(request)
            if not self._system_patterns[idx].search(system_text):
                return False

        if condition.message_count is not None:
            if not _compare_numeric(len(request.messages), condition.message_count):
                return False

        if condition.has_image is not None:
            req_has_image = _request_has_image(request)
            if req_has_image != condition.has_image:
                return False

        if condition.agent_id is not None:
            if not context.agent_id:
                return False
            if not fnmatch.fnmatch(context.agent_id, condition.agent_id):
                return False

        return True
