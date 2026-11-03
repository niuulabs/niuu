"""Numeric session usage projected from the existing durable runtime log.

Claude counts are per native response, not per assistant block. Codex's native
thread totals are cumulative and its cached input is already part of input.
The adapter supplies the previous projection for the native identity; no prompt
text, transcript scan or in-memory-only dedupe registry is needed here.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import UTC, datetime

COMPONENTS = ("input", "cache_read", "cache_write", "output")
MAX_SAFE_INTEGER = 2**53 - 1  # Public JSON counters must round-trip through clients.
CLAUDE_COMPONENTS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
)


def _integer(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= MAX_SAFE_INTEGER else None


def _number(value: object) -> float | None:
    if (
        type(value) not in (int, float)
        or not 0 <= value <= MAX_SAFE_INTEGER
        or not math.isfinite(value)
    ):
        return None
    return float(value)


def _counts(value: object, keys: tuple[str, ...] = COMPONENTS) -> dict | None:
    if not isinstance(value, dict) or not all(key in value for key in keys):
        return None
    values = [_integer(value[key]) for key in keys]
    if any(item is None for item in values):
        return None
    return dict(zip(COMPONENTS, values, strict=True))


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _model(value: object) -> str | None:
    """A runtime model id; Claude's locally written rows name ``<synthetic>``."""
    text = _text(value)
    return None if text is None or text.startswith("<") else text


def _instant(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return at.astimezone(UTC).isoformat() if at.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def observation(payload: dict, *, seq: int, at: datetime) -> dict | None:
    """Extract only authoritative main-session numeric observations.

    Result aggregates never stand in for the last response. Estimated tmux
    result counters are intentionally ignored; its native JSONL supplies usage.
    """
    if payload.get("parent_tool_use_id") or payload.get("isSidechain"):
        return None
    kind, subtype = _text(payload.get("type")), _text(payload.get("subtype"))
    native = _text(payload.get("session_id")) or _text(payload.get("thread_id"))
    base = {
        "seq": seq,
        "at": _instant(payload.get("timestamp")) or at.astimezone(UTC).isoformat(),
        "native": native,
    }
    if kind == "system" and subtype == "init" and not payload.get("_per_connect_handshake"):
        identity = _text(payload.get("uuid")) or native
        if identity:
            return {
                **base,
                "key": f"start:{identity}",
                "kind": "start",
                "model": _model(payload.get("model")),
                "engine": _text(payload.get("engine")),
            }
    message = payload.get("message")
    if (kind in {"assistant", "message_start"} or subtype == "claude_usage") and isinstance(
        message, dict
    ):
        identity = _text(message.get("id"))
        counts = _counts(message.get("usage"), CLAUDE_COMPONENTS)
        if not identity or counts is None:
            return None
        return {
            **base,
            "key": f"claude:{native or ''}:{identity}",
            "kind": "response",
            "engine": "claude",
            "model": _model(message.get("model")),
            "counts": counts,
            "last": counts,
            "caught_up": payload.get("caught_up", True) is not False,
        }
    if kind == "session_usage" and payload.get("engine") == "codex" and native:
        total, delta, last = (_counts(payload.get(key)) for key in ("total", "delta", "last"))
        identity = _text(payload.get("response_id")) or _text(payload.get("turn_id"))
        window = _integer(payload.get("window"))
        if total is None and (delta is None or not identity):
            if last is None and not window:
                return None
            # Without cumulative counters, a native turn + sample fingerprint
            # is the strongest available identity. Retain its first observation
            # durably, so non-contiguous replay cannot revive pre-clear context.
            sample = json.dumps([identity, payload.get("model"), last], sort_keys=True)
            fingerprint = hashlib.sha256(sample.encode()).hexdigest()
            return {
                **base,
                "key": f"context:{native}:{fingerprint}",
                "kind": "context",
                "engine": "codex",
                "model": _model(payload.get("model")),
                "last": last,
                "window": window if window else None,
            }
        return {
            **base,
            "key": f"codex:{native}" if total is not None else f"codex:{native}:{identity}",
            "kind": "cumulative" if total is not None else "response",
            "engine": "codex",
            "model": _model(payload.get("model")),
            "counts": total if total is not None else delta,
            "last": last,
            "window": window if window else None,
        }
    if kind == "result" and native:
        costs, windows = {}, {}
        model_usage = payload.get("modelUsage")
        for model, usage in (model_usage if isinstance(model_usage, dict) else {}).items():
            if not isinstance(usage, dict):
                continue
            cost, window = _number(usage.get("costUSD")), _integer(usage.get("contextWindow"))
            if cost is not None:
                costs[model] = cost
            if window:
                windows[model] = window
        cost = _number(payload.get("total_cost_usd"))
        if cost is None and costs:
            cost = sum(costs.values())
        if cost is not None or windows:
            return {
                **base,
                "key": f"cost:{native}",
                "kind": "cost",
                "cost": cost,
                "windows": windows,
            }
    if kind == "system" and subtype == "compact_boundary":
        identity = (
            _text(payload.get("compaction_id"))
            or _text(payload.get("uuid"))
            or _text(payload.get("turn_id"))
        )
        if not identity:
            # Never invent an identity from a broker sequence: transcript replay
            # receives new sequences and must not count a boundary twice.
            return None
        return {**base, "key": f"compact:{native or ''}:{identity}", "kind": "compaction"}
    if kind == "conversation_reset":
        identity = _text(payload.get("new_conversation_id")) or _text(payload.get("uuid"))
        if identity:
            return {**base, "key": f"reset:{identity}", "kind": "reset", "epoch": identity}
    if kind == "runtime_options":
        # Only a harness-acknowledged change made between turns counts: reads repeat
        # the current choice, and a change during a turn applies to the next one,
        # whose first response confirms it.
        current = payload.get("current")
        model = _model(current.get("model")) if isinstance(current, dict) else None
        if model and payload.get("applied") is True and payload.get("active_turn") is None:
            return {**base, "key": f"model:{seq}", "kind": "model", "model": model}
        return None
    if kind == "system" and subtype == "claude_usage_unavailable":
        return {
            **base,
            "key": f"unavailable:{seq}",
            "kind": "unavailable",
            "reason": _text(payload.get("reason")) or "usage_unavailable",
        }
    return None


def _max_counts(left: dict | None, right: dict | None) -> dict:
    return {key: max((left or {}).get(key, 0), (right or {}).get(key, 0)) for key in COMPONENTS}


def fold_usage(
    state: dict | None,
    event: dict,
    previous: dict | None,
    *,
    model_windows: dict[str, int],
) -> tuple[dict, dict]:
    """Return a compact session summary and merged native-identity snapshot.

    The snapshot lives on the existing log row's projection columns. Component
    maxima support partial streaming updates, non-contiguous repeated blocks,
    retries and replay at a different broker sequence without double billing.
    """
    result = deepcopy(state or {})
    internal = result.setdefault("_state", {})
    merged = deepcopy(previous or event)
    merged["first_seq"] = min((previous or {}).get("first_seq", event["seq"]), event["seq"])
    merged["first_at"] = min((previous or {}).get("first_at", event["at"]), event["at"])
    kind = event["kind"]
    event_order = [event["at"], event["seq"]]
    newest = event_order >= internal.get("last_order", ["", -1])
    if kind == "start":
        if previous is None and event_order > internal.get("start_order", ["", -1]):
            internal["start_seq"] = event["seq"]
            internal["start_order"] = event_order
            result["started_at"] = event["at"]
            if event.get("model"):
                result["model"] = event["model"]
            if event.get("engine"):
                result["engine"] = event["engine"]
        return result, merged
    if kind == "model":
        # Advance the order so an older response replayed later cannot revert it.
        if newest and result.get("model") != event["model"]:
            result["model"] = event["model"]
            internal["last_order"] = event_order
            internal.pop("last_key", None)
        return result, merged
    if kind == "unavailable":
        if newest:
            result["context"] = {"tokens": None, "window": None, "share": None}
            result["usage_available"] = False
            result["usage_reason"] = event["reason"]
        if event["reason"] != "incomplete_record":
            result["totals_complete"] = False
        return result, merged
    if kind == "reset":
        if previous is None and newest and event["seq"] >= internal.get("reset_seq", -1):
            internal.update(reset_seq=event["seq"], epoch=event["epoch"], boundary_at=event["at"])
            result["context"] = {"tokens": None, "window": None, "share": None}
            result["last_turn"] = None
            result["usage_available"] = False
            result["usage_reason"] = "reset_pending_usage"
            internal.pop("last_key", None)
        return result, merged
    if kind == "compaction":
        if previous is None:
            result["compactions"] = result.get("compactions", 0) + 1
            internal["boundary_at"] = max(internal.get("boundary_at", ""), event["at"])
            if newest:
                result["context"] = {"tokens": None, "window": None, "share": None}
                result["usage_available"] = False
                result["usage_reason"] = "compaction_pending_usage"
        return result, merged
    if kind == "cost":
        old_cost = (previous or {}).get("cost") or 0
        if event["cost"] is not None:
            cost = max(old_cost, event["cost"])
            merged["cost"] = cost
            result["cost_estimate"] = (result.get("cost_estimate") or 0) + (cost - old_cost)
        native_windows = internal.setdefault("windows", {}).setdefault(event["native"], {})
        if event_order >= (previous or {}).get("window_order", ["", -1]):
            native_windows.update(event["windows"])
            merged["window_order"] = event_order
        window = (
            native_windows.get(result.get("model"))
            if (event["native"] == internal.get("last_native"))
            else None
        )
        if window and result.get("context", {}).get("tokens") is not None:
            result["context"].update(window=window, share=result["context"]["tokens"] / window)
        return result, merged

    counts = _max_counts((previous or {}).get("counts"), event.get("counts"))
    old = (previous or {}).get("counts", {})
    if kind != "context":
        totals = result.setdefault("totals", dict.fromkeys(COMPONENTS, 0))
        for key in COMPONENTS:
            totals[key] += counts[key] - old.get(key, 0)
        merged["counts"] = counts
    result.setdefault("cost_estimate", None)
    if kind != "context":
        # A new broker may attach to an existing native conversation. Neither a
        # response nor an init frame proves the Forge session's full history was
        # captured. Report measured totals as a lower bound, never invent zero
        # historical compactions or claim a complete lifetime from one response.
        result.setdefault("totals_complete", False)

    # A cumulative regression is an old snapshot (or an unidentifiable native
    # counter reset). Keep lifetime high-water totals and never advertise it as
    # a fresh current response. Explicit /clear gives the next stream an epoch.
    stale = (
        kind == "cumulative"
        and previous is not None
        and any(event["counts"][key] < old.get(key, 0) for key in COMPONENTS)
    )
    if stale:
        result["totals_complete"] = False
        if newest and event["native"] == internal.get("last_native"):
            result["context"] = {"tokens": None, "window": None, "share": None}
            result["usage_available"] = False
            result["usage_reason"] = "native_counter_regression"
        return result, merged
    order = (
        [event["at"], event["seq"]]
        if kind == "cumulative"
        else [merged["first_at"], merged["first_seq"]]
    )
    if kind == "cumulative":
        # Equal cumulative snapshots are replay, even when delivered with a new
        # broker timestamp after compaction or /clear. Only advanced counters
        # establish a new measured response.
        advanced = previous is None or any(counts[k] > old.get(k, 0) for k in COMPONENTS)
        if advanced:
            merged["sample_order"] = order
        else:
            order = (previous or {}).get("sample_order", [merged["first_at"], merged["first_seq"]])
    last_key = internal.get("last_key")
    is_latest = (kind == "response" and last_key == event["key"]) or order >= internal.get(
        "last_order", ["", -1]
    )
    if (
        event["seq"] < internal.get("reset_seq", -1)
        or not is_latest
        or order[0] < internal.get("boundary_at", "")
    ):
        return result, merged
    # The newest response names the model even while the transcript catches up:
    # a later row may name none (Claude's ``<synthetic>`` rows).
    result["model"] = event.get("model") or result.get("model")
    if event.get("caught_up") is False:
        result["context"] = {"tokens": None, "window": None, "share": None}
        result["usage_available"] = False
        result["usage_reason"] = "transcript_catching_up"
        return result, merged
    last = event.get("last")
    if kind == "context" and last is None:
        # A window-only update has no new response, so it may refine a known
        # window but cannot clear or revive context across a boundary.
        if (
            event["native"] == internal.get("last_native")
            and result.get("context", {}).get("tokens") is not None
        ):
            window = event["window"]
            result["context"].update(window=window, share=result["context"]["tokens"] / window)
        return result, merged
    if kind == "response" and last is not None:
        last = counts
    result["engine"] = event["engine"]
    result["last_turn"] = last
    window = (
        event.get("window")
        or internal.get("windows", {}).get(event["native"], {}).get(result["model"])
        or model_windows.get(result["model"])
    )
    tokens = sum(last[key] for key in COMPONENTS if key != "output") if last is not None else None
    result["context"] = {
        "tokens": tokens,
        "window": window,
        "share": tokens / window if tokens is not None and window else None,
    }
    result["usage_available"] = last is not None
    result.pop("usage_reason", None)
    internal.update(last_key=event["key"], last_order=order, last_native=event["native"])
    return result, merged


def projection_key(event: dict, state: dict | None) -> str:
    """Use a bounded database key without losing identity across retries."""
    # Native session identity changes after /clear. Salting historical snapshots
    # with the *current* reset epoch would bill an old replay a second time.
    return hashlib.sha256(event["key"].encode()).hexdigest()


def parse_json(value: object) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}
