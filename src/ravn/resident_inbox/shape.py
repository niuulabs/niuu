"""Structural shape derivation and aggregate folding for resident inbox slots.

A *shape* is the structure of an observation: its source, its kind, and the set
of field paths it carries with their value types.  Two observations share a
pending queue slot only when their shapes are identical.

Nothing here interprets meaning.  A shape says two observations are structurally
comparable; it never says either of them is routine, safe, or ignorable.  The
aggregates summarise variation *within* one identical shape and never merge
across shapes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

#: Values folded into numeric ranges.  ``bool`` is deliberately excluded: it is
#: an ``int`` subclass in Python, but a flag flipping is a categorical change,
#: not a range.
_NUMERIC_TYPES = (int, float)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


def field_paths(payload: Any, *, prefix: str = "") -> tuple[tuple[str, str], ...]:
    """Return sorted ``(dotted path, type name)`` pairs for one payload.

    Arrays contribute a single ``path[]`` entry typed by their first element, so
    a list that merely grows does not change the shape while a list whose
    element type changes does.
    """
    if isinstance(payload, dict):
        paths: list[tuple[str, str]] = []
        for key in sorted(payload):
            child = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(field_paths(payload[key], prefix=child))
        return tuple(paths)
    if isinstance(payload, list):
        element = payload[0] if payload else None
        marker = f"{prefix}[]" if prefix else "[]"
        if isinstance(element, dict | list):
            return field_paths(element, prefix=marker)
        return ((marker, _type_name(element) if payload else "empty"),)
    return ((prefix, _type_name(payload)),)


def shape_key(
    *,
    source: str,
    kind: str,
    payload: Any,
    distinct_id: str = "",
) -> str:
    """Derive the stable slot key for one observation.

    ``distinct_id`` forces a dedicated slot.  Callers pass it for observations
    that must never fold into a shared slot regardless of structure — directed
    operator messages, whose channel carries authority and must not be
    summarised away.
    """
    digest = hashlib.sha256()
    digest.update(source.encode())
    digest.update(b"\x00")
    digest.update(kind.encode())
    digest.update(b"\x00")
    digest.update(distinct_id.encode())
    for path, type_name in field_paths(payload):
        digest.update(b"\x00")
        digest.update(path.encode())
        digest.update(b"\x1f")
        digest.update(type_name.encode())
    return digest.hexdigest()[:16]


@dataclass(frozen=True)
class ShapeAggregate:
    """Domain-neutral variation summary for observations sharing one shape."""

    numeric: dict[str, tuple[float, float]] = field(default_factory=dict)
    categorical: dict[str, tuple[str, ...]] = field(default_factory=dict)
    high_cardinality: tuple[str, ...] = ()
    #: ``"<path>:min"`` / ``"<path>:max"`` → the archive reference of the
    #: observation that set that bound. A reference, not a copy: the archive
    #: already holds every observation, and pointing at it means the number of
    #: entries is bounded by the slot's own field structure with nothing to
    #: configure and no field left without context.
    extreme_refs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "numeric": {path: [low, high] for path, (low, high) in sorted(self.numeric.items())},
            "categorical": {
                path: list(values) for path, values in sorted(self.categorical.items())
            },
            "high_cardinality": list(self.high_cardinality),
            "extreme_refs": dict(sorted(self.extreme_refs.items())),
        }

    @classmethod
    def from_dict(cls, data: Any) -> ShapeAggregate:
        if not isinstance(data, dict):
            return cls()
        numeric: dict[str, tuple[float, float]] = {}
        for path, bounds in dict(data.get("numeric") or {}).items():
            if isinstance(bounds, list | tuple) and len(bounds) == 2:
                numeric[str(path)] = (float(bounds[0]), float(bounds[1]))
        categorical = {
            str(path): tuple(str(item) for item in values or ())
            for path, values in dict(data.get("categorical") or {}).items()
        }
        # Slots written before extremes became references carried whole payload
        # copies under ``extreme_payloads``. Those are redundant with the
        # archive, so they are dropped rather than migrated.
        refs = {
            str(key): str(value)
            for key, value in dict(data.get("extreme_refs") or {}).items()
            if str(value or "").strip()
        }
        return cls(
            numeric=numeric,
            categorical=categorical,
            high_cardinality=tuple(str(item) for item in data.get("high_cardinality") or ()),
            extreme_refs=refs,
        )


def _flatten_values(payload: Any, *, prefix: str = "") -> dict[str, Any]:
    if isinstance(payload, dict):
        flat: dict[str, Any] = {}
        for key, value in payload.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            flat.update(_flatten_values(value, prefix=child))
        return flat
    if isinstance(payload, list):
        marker = f"{prefix}[]" if prefix else "[]"
        if payload and isinstance(payload[0], dict | list):
            return _flatten_values(payload[0], prefix=marker)
        return {marker: payload[0] if payload else None}
    return {prefix: payload}


def numeric_novelty(
    aggregate: ShapeAggregate,
    payload: Any,
    *,
    observation_count: int,
    min_observations: int,
) -> tuple[str, float] | None:
    """Return the first ``(path, value)`` that lies outside the slot's history.

    A structurally identical observation whose *values* leave the range the slot
    has already established is the one thing coalescing would otherwise bury: a
    temperature going 42 → 200 changes no field and no type, so it folds in and
    merely widens a bound. Treating it as novel gives it its own slot and its
    own wake.

    This is novelty against the slot's own observed history — it makes no claim
    about what any field means. It stays quiet until the slot has seen
    ``min_observations``, because a range drawn from a handful of samples is not
    yet a range, and it allows drift of one span beyond each bound so ordinary
    movement does not fragment the queue. A bound that has never moved
    (``0 → 0`` across thousands of observations) has zero span, so any departure
    from it is novel — which is exactly the behaviour wanted for an error code.
    """
    if observation_count < max(1, min_observations):
        return None
    for path, value in _flatten_values(payload).items():
        if isinstance(value, bool) or value is None or not isinstance(value, _NUMERIC_TYPES):
            continue
        bounds = aggregate.numeric.get(path)
        if bounds is None:
            continue
        low, high = bounds
        span = high - low
        number = float(value)
        if number < low - span or number > high + span:
            return path, number
    return None


def fold_aggregate(
    aggregate: ShapeAggregate,
    payload: Any,
    *,
    archive_ref: str,
    max_distinct_values: int,
) -> ShapeAggregate:
    """Fold one observation's values into the running aggregate.

    Numeric paths keep their observed range and a reference to the archived
    observation at each extreme, so an excursion is never summarised away.
    Categorical paths keep their distinct values up to a cap; above it the path
    is recorded as high-cardinality rather than growing without bound.
    """
    numeric = dict(aggregate.numeric)
    categorical = {path: list(values) for path, values in aggregate.categorical.items()}
    high_cardinality = set(aggregate.high_cardinality)
    extremes = dict(aggregate.extreme_refs)

    for path, value in _flatten_values(payload).items():
        if isinstance(value, bool) or value is None:
            value = str(value)
        if isinstance(value, _NUMERIC_TYPES):
            number = float(value)
            previous = numeric.get(path)
            if previous is None:
                numeric[path] = (number, number)
                extremes[f"{path}:min"] = archive_ref
                extremes[f"{path}:max"] = archive_ref
                continue
            low, high = previous
            if number < low:
                numeric[path] = (number, high)
                extremes[f"{path}:min"] = archive_ref
            elif number > high:
                numeric[path] = (low, number)
                extremes[f"{path}:max"] = archive_ref
            continue
        if path in high_cardinality:
            continue
        text = str(value)
        values = categorical.setdefault(path, [])
        if text in values:
            continue
        if len(values) >= max_distinct_values:
            high_cardinality.add(path)
            categorical.pop(path, None)
            continue
        values.append(text)

    return ShapeAggregate(
        numeric=numeric,
        categorical={path: tuple(values) for path, values in categorical.items()},
        high_cardinality=tuple(sorted(high_cardinality)),
        extreme_refs=extremes,
    )


def aggregate_summary_lines(aggregate: ShapeAggregate) -> list[str]:
    """Render the aggregate for a resident prompt; structural inventory first."""
    lines: list[str] = []
    if aggregate.numeric:
        rendered = ", ".join(
            f"{path} {_number(low)}–{_number(high)}"
            for path, (low, high) in sorted(aggregate.numeric.items())
        )
        lines.append(f"  numeric ranges: {rendered}")
    if aggregate.categorical:
        rendered = ", ".join(
            f"{path} {{{', '.join(values)}}}"
            for path, values in sorted(aggregate.categorical.items())
        )
        lines.append(f"  categorical values: {rendered}")
    if aggregate.high_cardinality:
        lines.append(f"  high-cardinality paths: {', '.join(aggregate.high_cardinality)}")
    return lines


def _number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.4g}"
