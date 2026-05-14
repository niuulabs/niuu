"""Safe condition expression evaluator for pipeline stage conditions.

Supports a minimal expression language:

- ``stages.<name>.verdict == pass``
- ``stages.<name>.critical_count == 0``
- ``stages.<name>.verdict != fail``
- Numeric comparisons: ``==``, ``!=``, ``<``, ``<=``, ``>``, ``>=``
- Logical combinators: ``AND``, ``OR`` (case-insensitive)

The evaluator does NOT use ``eval()`` — it parses to an AST and interprets
it directly, so untrusted YAML cannot execute arbitrary Python.

Usage::

    ctx = {
        "stages": {
            "review": {"verdict": "pass", "findings_count": 3},
            "test": {"verdict": "pass"},
        }
    }
    result = evaluate_condition(
        "stages.review.verdict == pass AND stages.test.verdict == pass", ctx
    )
    # True
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Token types
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    (?P<AND>\bAND\b|\band\b)
    |(?P<OR>\bOR\b|\bor\b)
    |(?P<FIELD>stages\.[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_]+)
    |(?P<OP><=|>=|!=|==|<|>)
    |(?P<VALUE>[a-zA-Z0-9_\-\.]+)
    |(?P<LPAREN>\()
    |(?P<RPAREN>\))
    """,
    re.VERBOSE,
)


@dataclass
class _Token:
    kind: str
    value: str


def _tokenize(expr: str) -> list[_Token]:
    """Tokenize an expression string."""
    tokens = []
    for m in _TOKEN_RE.finditer(expr):
        kind = m.lastgroup
        assert kind is not None
        tokens.append(_Token(kind=kind, value=m.group()))
    return tokens


# ---------------------------------------------------------------------------
# Parser / evaluator
# ---------------------------------------------------------------------------


class ConditionError(ValueError):
    """Raised when a condition expression cannot be parsed or evaluated."""


def _resolve_field(field_path: str, context: dict[str, Any]) -> Any:
    """Resolve a dotted field path like ``stages.review.verdict`` from context."""
    parts = field_path.split(".")
    value: Any = context
    for part in parts:
        if not isinstance(value, dict):
            raise ConditionError(f"Cannot index into non-dict at '{part}' in path '{field_path}'")
        value = value.get(part)
        if value is None:
            return None
    return value


def _compare(left: Any, op: str, right_str: str) -> bool:
    """Compare *left* against *right_str* using *op*.

    Numeric comparison is attempted first; falls back to string comparison.
    """
    # Try numeric comparison
    try:
        left_num = float(str(left))
        right_num = float(right_str)
        match op:
            case "==":
                return left_num == right_num
            case "!=":
                return left_num != right_num
            case "<":
                return left_num < right_num
            case "<=":
                return left_num <= right_num
            case ">":
                return left_num > right_num
            case ">=":
                return left_num >= right_num
    except (ValueError, TypeError):
        pass

    # String comparison
    left_str = str(left) if left is not None else ""
    match op:
        case "==":
            return left_str == right_str
        case "!=":
            return left_str != right_str
        case "<":
            return left_str < right_str
        case "<=":
            return left_str <= right_str
        case ">":
            return left_str > right_str
        case ">=":
            return left_str >= right_str
    raise ConditionError(f"Unknown operator: {op!r}")


class _Parser:
    """Recursive-descent parser for condition expressions."""

    def __init__(self, tokens: list[_Token], context: dict[str, Any]) -> None:
        self._tokens = tokens
        self._pos = 0
        self._context = context

    def _peek(self) -> _Token | None:
        if self._pos >= len(self._tokens):
            return None
        return self._tokens[self._pos]

    def _consume(self) -> _Token:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def parse(self) -> bool:
        result = self._parse_or()
        if self._pos < len(self._tokens):
            remaining = " ".join(t.value for t in self._tokens[self._pos :])
            raise ConditionError(f"Unexpected tokens after expression: {remaining!r}")
        return result

    def _parse_or(self) -> bool:
        left = self._parse_and()
        while (tok := self._peek()) and tok.kind == "OR":
            self._consume()
            right = self._parse_and()
            left = left or right
        return left

    def _parse_and(self) -> bool:
        left = self._parse_atom()
        while (tok := self._peek()) and tok.kind == "AND":
            self._consume()
            right = self._parse_atom()
            left = left and right
        return left

    def _parse_atom(self) -> bool:
        tok = self._peek()
        if tok is None:
            raise ConditionError("Unexpected end of expression")

        if tok.kind == "LPAREN":
            self._consume()
            result = self._parse_or()
            close = self._peek()
            if close is None or close.kind != "RPAREN":
                raise ConditionError("Missing closing parenthesis")
            self._consume()
            return result

        if tok.kind == "FIELD":
            return self._parse_comparison()

        raise ConditionError(f"Unexpected token: {tok.value!r} (kind={tok.kind})")

    def _parse_comparison(self) -> bool:
        field_tok = self._consume()
        op_tok = self._peek()
        if op_tok is None or op_tok.kind != "OP":
            raise ConditionError(
                f"Expected operator after field '{field_tok.value}', got {op_tok!r}"
            )
        self._consume()
        val_tok = self._peek()
        if val_tok is None or val_tok.kind not in ("VALUE", "FIELD"):
            raise ConditionError(f"Expected value after operator '{op_tok.value}', got {val_tok!r}")
        self._consume()

        left_value = _resolve_field(field_tok.value, self._context)
        right_str = val_tok.value
        return _compare(left_value, op_tok.value, right_str)


def evaluate_condition(expr: str, context: dict[str, Any]) -> bool:
    """Evaluate a condition expression against *context*.

    :param expr: Condition string (e.g. ``"stages.review.verdict == pass"``).
    :param context: Dict containing stage outcomes, e.g.
        ``{"stages": {"review": {"verdict": "pass"}}}``.
    :returns: ``True`` if the condition is satisfied, ``False`` otherwise.
    :raises ConditionError: When the expression cannot be parsed or evaluated.
    """
    if not expr or not expr.strip():
        return True

    tokens = _tokenize(expr.strip())
    if not tokens:
        return True

    parser = _Parser(tokens, context)
    return parser.parse()
