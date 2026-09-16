"""Operator preview binds its inputs and exports only verified public projection."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts.forge_text_projection import preview
from tests.test_domain.test_text_projection_repair import captured


def evidence():
    frames, original, candidate = captured()
    rows = [vars(frame) for frame in frames]
    rows.append(
        {
            "session_id": "session",
            "seq": 10,
            "kind": "conversation.turn",
            "payload": {"turn": original},
            "ts": None,
            "request_id": None,
        }
    )
    native = []
    for index, part in enumerate(part for part in candidate["parts"] if part["type"] == "text"):
        native.append(
            SimpleNamespace(
                payload={
                    "message": {
                        "content": [
                            {
                                **part,
                                "id": f"native-{index}",
                                "id_source": "native",
                                "phase": "commentary" if index == 0 else "final_answer",
                            }
                        ]
                    }
                }
            )
        )
    return rows, {"turns": [original]}, native


def test_preview_enriches_verified_occurrences_and_preserves_raw_inputs():
    rows, history, native = evidence()
    before = deepcopy((rows, history))
    result = preview(rows, history, native, "a" * 64)
    assert (rows, history) == before
    assert result["summary"]["repaired_turns"] == 1
    assert result["summary"]["checks"][0]["native_identity_and_phase_verified"] is True
    texts = [part for part in result["history"]["turns"][0]["parts"] if part["type"] == "text"]
    assert [part["id"] for part in texts] == ["native-0", "native-1"]
    assert [part["phase"] for part in texts] == ["commentary", "final_answer"]
    assert result["marker"]["repairs"][0]["proof"]["native_prefix_sha256"] == "a" * 64


def test_preview_restores_typed_database_timestamps_from_export_spelling():
    rows, history, native = evidence()
    for row in rows:
        row["ts"] = "2026-09-08 00:00:00+00:00"
    tool, output = history["turns"][0]["parts"]
    tool.update(
        started_at="2026-09-08T00:00:00+00:00",
        ended_at="2026-09-08T00:00:00+00:00",
        duration_ms=0,
    )
    output["ended_at"] = "2026-09-08T00:00:00+00:00"
    result = preview(rows, history, native, "a" * 64)
    assert result["summary"]["repaired_turns"] == 1
    assert rows[0]["ts"] == "2026-09-08 00:00:00+00:00"


@pytest.mark.parametrize("corruption", ["gap", "foreign_session", "different_cache"])
def test_preview_rejects_unbound_or_incomplete_evidence(corruption):
    rows, history, native = evidence()
    if corruption == "gap":
        rows.pop(2)
    elif corruption == "foreign_session":
        rows[0]["session_id"] = "foreign"
    else:
        history = deepcopy(history)
        history["turns"][0]["parts"][0]["input"] = {"cmd": "different"}
        # A differing cache cannot itself prove a repair, so leave it unchanged.
        result = preview(rows, history, native, "a" * 64)
        assert result["summary"]["repaired_turns"] == 0
        assert result["history"]["turns"] == history["turns"]
        return
    with pytest.raises(ValueError):
        preview(rows, history, native, "a" * 64)
