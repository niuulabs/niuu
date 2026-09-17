"""History delivery controls are not agent outcomes or conversational content."""


def is_history_control(kind: str, payload: dict | None) -> bool:
    """Recognize the typed contract, including retained pre-contract log entries.

    Do not match prose: a user or assistant may legitimately discuss this error.
    Raw log evidence remains retained; only its conversation/wire projection omits
    connection-specific recovery controls.
    """
    return kind == "history_gap" or (
        kind == "error"
        and isinstance(payload, dict)
        and payload.get("code") == "conversation_history_too_large"
    )


def history_gap(reason: str, *, head_seq: int | None = None) -> dict:
    return {
        "type": "history_gap",
        "history_protocol": 2,
        "reason": reason,
        "recovery": "recent",
        "head_seq": head_seq,
        "projection_revision": None,
        "_per_connect_handshake": True,
    }
