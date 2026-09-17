"""Tests for shallow conversation serialization + the lazy tool-result endpoint.

Shallow mode elides heavy ``tool_result`` content to placeholders so a client
can render the conversation structure cheaply and lazy-load an individual result
on demand. Covers the pure helper (``skuld.conversation_shallow``) and the two
broker endpoints (``GET /api/conversation/history?detail=shallow`` and
``GET /api/conversation/tool-result/{tool_use_id}``).
"""

import json

import pytest
from fastapi.testclient import TestClient

from skuld.broker import ConversationTurn, app, broker
from skuld.conversation_shallow import (
    INLINE_BYTE_LIMIT,
    _content_image_info,
    elide_tool_result_block,
    elide_turns,
    is_elided_block,
)

BIG = "X" * (INLINE_BYTE_LIMIT + 1000)  # safely over the inline threshold
SMALL = "ok"

# A Skuld image Read returns `content` as a JSON STRING of this envelope (verified against the
# live REST API). The base64 is padded so the serialized content clears INLINE_BYTE_LIMIT and
# is therefore always elided — exactly the case the image hint exists to describe.
IMG_ENVELOPE = json.dumps(
    {
        "type": "image",
        "file": {
            "base64": "iVBORw0KGgo" + "A" * 2000,
            "type": "image/png",
            "dimensions": {
                "displayWidth": 920,
                "displayHeight": 2000,
                "originalWidth": 1206,
                "originalHeight": 2622,
            },
            "originalSize": 900660,
        },
    }
)
# A Read of a TEXT file uses the same envelope with `{"file":{"content":…}}` and is NOT an image.
TEXT_ENVELOPE = json.dumps({"type": "text", "file": {"content": "import SwiftUI\n" * 200}})


def _tool_use(uid: str) -> dict:
    return {"type": "tool_use", "id": uid, "name": "Bash", "input": {"command": "ls"}}


def _tool_result(uid: str, content, is_error: bool = False, **extra) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": uid,
        "content": content,
        "is_error": is_error,
        **extra,
    }


class TestElideHelper:
    """Pure-function behaviour of the elide helper."""

    def test_big_tool_result_becomes_placeholder(self):
        block = _tool_result("t1", BIG)
        out = elide_tool_result_block(block)
        assert out["type"] == "tool_result"
        assert out["tool_use_id"] == "t1"
        assert out["truncated"] is True
        assert out["byte_size"] == len(BIG.encode("utf-8"))
        assert out["preview"] == BIG[:200]
        assert "content" not in out
        assert is_elided_block(out)

    def test_small_tool_result_kept_inline(self):
        block = _tool_result("t2", SMALL)
        out = elide_tool_result_block(block)
        assert out == block  # unchanged — not worth a round-trip
        assert not is_elided_block(out)

    def test_non_tool_result_untouched(self):
        for block in (_tool_use("t3"), {"type": "text", "text": "hi"}):
            assert elide_tool_result_block(block) == block

    def test_list_content_preview_and_size(self):
        content = [{"type": "text", "text": "A" * 6000}]
        out = elide_tool_result_block(_tool_result("t4", content))
        assert out["truncated"] is True
        assert out["preview"].startswith("AAAA")
        assert len(out["preview"]) == 200
        assert out["byte_size"] > INLINE_BYTE_LIMIT

    def test_error_flag_preserved(self):
        out = elide_tool_result_block(_tool_result("t5", BIG, is_error=True))
        assert out["is_error"] is True

    def test_elide_turns_does_not_mutate_input(self):
        turns = [{"role": "assistant", "parts": [_tool_use("t1"), _tool_result("t1", BIG)]}]
        out = elide_turns(turns)
        # input still has the full content (no mutation)
        assert turns[0]["parts"][1]["content"] == BIG
        # output is elided
        assert is_elided_block(out[0]["parts"][1])
        # the tool_use call is preserved verbatim
        assert out[0]["parts"][0] == _tool_use("t1")


class TestImageHint:
    """The image hint stamped onto an elided image tool_result placeholder."""

    def test_image_read_placeholder_carries_hint(self):
        out = elide_tool_result_block(_tool_result("img", IMG_ENVELOPE))
        assert out["truncated"] is True
        assert "content" not in out
        assert out["is_image"] is True
        assert out["mime_type"] == "image/png"
        assert out["img_w"] == 920
        assert out["img_h"] == 2000

    def test_text_read_placeholder_has_no_hint(self):
        out = elide_tool_result_block(_tool_result("txt", TEXT_ENVELOPE))
        assert out["truncated"] is True
        assert "is_image" not in out
        assert "mime_type" not in out

    def test_non_json_big_string_has_no_hint(self):
        out = elide_tool_result_block(_tool_result("plain", BIG))
        assert out["truncated"] is True
        assert "is_image" not in out

    def test_anthropic_content_array_form_detected(self):
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": "/9j/4AAQ" + "A" * 2000,
                },
            },
        ]
        out = elide_tool_result_block(_tool_result("aimg", content))
        assert out["is_image"] is True
        assert out["mime_type"] == "image/jpeg"
        assert "img_w" not in out  # array form carries no display dimensions

    def test_content_image_info_pure_helper(self):
        assert _content_image_info(IMG_ENVELOPE) == (True, "image/png", 920, 2000)
        assert _content_image_info(TEXT_ENVELOPE) == (False, None, None, None)
        assert _content_image_info("not json {") == (False, None, None, None)
        assert _content_image_info(SMALL) == (False, None, None, None)

    def test_hint_survives_shallow_endpoint(self):
        broker._conversation_turns = [
            ConversationTurn(
                id="a1",
                role="assistant",
                content="rendered a chart",
                parts=[_tool_use("chart"), _tool_result("chart", IMG_ENVELOPE)],
            ),
        ]
        try:
            client = TestClient(app, raise_server_exceptions=False)
            data = client.get("/api/conversation/history", params={"detail": "shallow"}).json()
            part = next(p for p in data["turns"][0]["parts"] if p.get("tool_use_id") == "chart")
            assert part["is_image"] is True
            assert part["mime_type"] == "image/png"
            assert part["img_w"] == 920 and part["img_h"] == 2000
        finally:
            broker._conversation_turns = []


class TestShallowEndpoints:
    """The broker conversation + tool-result HTTP surface."""

    @pytest.fixture(autouse=True)
    def _reset_broker(self):
        broker._conversation_turns = []
        yield
        broker._conversation_turns = []

    def _seed(self):
        broker._conversation_turns = [
            ConversationTurn(id="u1", role="user", content="do it"),
            ConversationTurn(
                id="a1",
                role="assistant",
                content="working",
                parts=[
                    _tool_use("big"),
                    _tool_result("big", BIG),
                    _tool_use("small"),
                    _tool_result("small", SMALL),
                    {"type": "text", "text": "done"},
                ],
            ),
        ]

    def test_full_mode_keeps_content(self):
        self._seed()
        client = TestClient(app, raise_server_exceptions=False)
        data = client.get("/api/conversation/history").json()
        parts = data["turns"][1]["parts"]
        big = next(p for p in parts if p.get("tool_use_id") == "big")
        assert big["content"] == BIG
        assert "truncated" not in big

    def test_shallow_mode_elides_big_keeps_small(self):
        self._seed()
        client = TestClient(app, raise_server_exceptions=False)
        data = client.get("/api/conversation/history", params={"detail": "shallow"}).json()
        parts = data["turns"][1]["parts"]
        big = next(p for p in parts if p.get("tool_use_id") == "big")
        small = next(p for p in parts if p.get("tool_use_id") == "small")
        text = next(p for p in parts if p.get("type") == "text")
        # big elided
        assert big["truncated"] is True
        assert "content" not in big
        assert big["byte_size"] == len(BIG.encode("utf-8"))
        # small inline, text + tool_use untouched
        assert small["content"] == SMALL
        assert text["text"] == "done"
        assert any(p.get("type") == "tool_use" and p.get("id") == "big" for p in parts)

    def test_shallow_payload_is_much_smaller(self):
        self._seed()
        client = TestClient(app, raise_server_exceptions=False)
        full = client.get("/api/conversation/history").content
        shallow = client.get("/api/conversation/history", params={"detail": "shallow"}).content
        assert len(shallow) < len(full) / 2

    def test_tool_result_fetch_returns_full_content(self):
        self._seed()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/conversation/tool-result/big")
        assert resp.status_code == 200
        body = resp.json()
        assert body["tool_use_id"] == "big"
        assert body["content"] == BIG
        assert body["is_error"] is False

    def test_tool_result_fetch_unknown_id_404(self):
        self._seed()
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/api/conversation/tool-result/nope").status_code == 404


# ── P1 (2026-07-12): tool_use INPUT elision ─────────────────────────────────────────────────
# Measured on the lexi-frontend-presentation session, tool_use.input was 59% of a 1.5 MB
# shallow window (Edit old/new strings, Write bodies) while the UI renders one bounded
# argument line per collapsed card. Shallow now elides heavy inputs to
# {"_elided_input": true, "byte_size": N, "preview": <primary arg>} — still a dict — and the
# per-result lazy endpoint carries the FULL input for the expand.

from skuld.conversation_shallow import elide_tool_use_block, is_elided_input  # noqa: E402

BIG_INPUT = {"file_path": "/repo/App.swift", "old_string": "Y" * 3000, "new_string": "Z" * 3000}


class TestToolUseInputElision:
    def test_big_input_elides_to_placeholder_with_primary_arg_preview(self):
        block = {"type": "tool_use", "id": "e1", "name": "Edit", "input": dict(BIG_INPUT)}
        out = elide_tool_use_block(block)
        assert is_elided_input(out["input"])
        assert out["input"]["byte_size"] > INLINE_BYTE_LIMIT
        # preview = the primary argument line (file_path outranks the heavy strings)
        assert out["input"]["preview"] == "/repo/App.swift"
        # everything else on the block is untouched
        assert out["id"] == "e1" and out["name"] == "Edit" and out["type"] == "tool_use"

    def test_small_input_passes_through_and_elision_is_idempotent(self):
        small = {"type": "tool_use", "id": "s1", "name": "Bash", "input": {"command": "ls"}}
        assert elide_tool_use_block(small) is small
        big = elide_tool_use_block(
            {
                "type": "tool_use",
                "id": "e2",
                "name": "Write",
                "input": {"file_path": "/f", "content": "W" * 3000},
            }
        )
        again = elide_tool_use_block(big)
        assert again["input"] == big["input"], "an already-elided input never re-elides"

    def test_shallow_endpoint_elides_input_and_full_keeps_it(self):
        broker._conversation_turns = [
            ConversationTurn(
                id="a9",
                role="assistant",
                content="editing",
                parts=[
                    {"type": "tool_use", "id": "edit9", "name": "Edit", "input": dict(BIG_INPUT)},
                    _tool_result("edit9", SMALL),
                ],
            ),
        ]
        client = TestClient(app, raise_server_exceptions=False)
        full = client.get("/api/conversation/history").json()
        shallow = client.get("/api/conversation/history", params={"detail": "shallow"}).json()
        full_use = next(p for p in full["turns"][0]["parts"] if p.get("id") == "edit9")
        shallow_use = next(p for p in shallow["turns"][0]["parts"] if p.get("id") == "edit9")
        assert full_use["input"]["old_string"] == "Y" * 3000
        assert is_elided_input(shallow_use["input"])
        assert shallow_use["input"]["preview"] == "/repo/App.swift"

    def test_tool_result_fetch_carries_the_full_input(self):
        broker._conversation_turns = [
            ConversationTurn(
                id="a10",
                role="assistant",
                content="editing",
                parts=[
                    {"type": "tool_use", "id": "edit10", "name": "Edit", "input": dict(BIG_INPUT)}
                ],
            ),
            ConversationTurn(
                id="u10",
                role="user",
                content="",
                parts=[_tool_result("edit10", SMALL)],
            ),
        ]
        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/api/conversation/tool-result/edit10").json()
        assert body["content"] == SMALL
        assert body["input"]["old_string"] == "Y" * 3000, "use + result live in different turns"

    def test_input_only_hit_returns_200_for_running_tool(self):
        broker._conversation_turns = [
            ConversationTurn(
                id="a11",
                role="assistant",
                content="running",
                parts=[
                    {
                        "type": "tool_use",
                        "id": "run11",
                        "name": "Write",
                        "input": {"file_path": "/f", "content": "W" * 3000},
                    }
                ],
            ),
        ]
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/conversation/tool-result/run11")
        assert resp.status_code == 200
        assert resp.json()["content"] == ""
        assert resp.json()["input"]["file_path"] == "/f"


# --------------------------------------------------------------------------- D1 tool timing
#
# Shallow is the mode the iOS transcript actually loads, so per-tool timing is only real if it
# survives elision. ``elide_tool_use_block`` spreads ``**block`` and keeps it for free — but
# ``elide_tool_result_block`` REBUILDS its placeholder from scratch, the same trap the image
# hint had to be threaded through, so the ``ended_at`` copy needs its own pin.

STARTED = "2026-08-03T10:00:00+00:00"
ENDED = "2026-08-03T10:03:12+00:00"


class TestAskUserQuestionNeverElided:
    """A question's input IS the UI — eliding it deletes the card, not a payload.

    Regression for the live `agents-diet` session (2026-08-11): a four-question ask
    with per-option descriptions serialised to 3387 B, blew the 1 KB limit, and the
    client had nothing to render a card from while the agent sat blocked on an
    answer. The perverse property this guards: the richer the question, the more
    certainly it used to vanish.
    """

    ASK_INPUT = {
        "questions": [
            {
                "question": "Are eggs and dairy in or out?",
                "header": "Diet scope",
                "options": [
                    {"label": f"option {i}", "description": "why this matters " * 20}
                    for i in range(4)
                ],
            }
        ]
    }

    def _ask_block(self, name="AskUserQuestion", block_id="q1"):
        return {
            "type": "tool_use",
            "id": block_id,
            "name": name,
            "input": dict(self.ASK_INPUT),
        }

    def test_oversized_ask_input_is_kept_inline(self):
        assert len(json.dumps(self.ASK_INPUT)) > INLINE_BYTE_LIMIT, "fixture must exceed limit"
        out = elide_tool_use_block(self._ask_block())
        assert not is_elided_input(out["input"]), "the ask must survive shallow serialization"
        first = out["input"]["questions"][0]["options"][0]["description"]
        assert first.startswith("why this matters")

    def test_exemption_is_name_normalized(self):
        for name in ("AskUserQuestion", "ask_user_question", "  askuserquestion  "):
            out = elide_tool_use_block(self._ask_block(name=name))
            assert not is_elided_input(out["input"]), name

    def test_other_tools_still_elide(self):
        """The exemption is surgical — it must not disarm the payload diet."""
        out = elide_tool_use_block(
            {"type": "tool_use", "id": "e", "name": "Edit", "input": dict(BIG_INPUT)}
        )
        assert is_elided_input(out["input"])

    def test_shallow_endpoint_keeps_the_ask_renderable(self):
        broker._conversation_turns = [
            ConversationTurn(
                id="aq",
                role="assistant",
                content="asking",
                parts=[self._ask_block(block_id="ask1")],
            ),
        ]
        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/api/conversation/history?detail=shallow").json()
        blocks = [
            b
            for t in body["turns"]
            for b in t.get("parts", [])
            if b.get("type") == "tool_use" and b.get("id") == "ask1"
        ]
        assert len(blocks) == 1
        assert not is_elided_input(blocks[0]["input"])
        assert blocks[0]["input"]["questions"][0]["question"] == "Are eggs and dairy in or out?"


class TestToolTimingSurvivesElision:
    def test_tool_use_timing_passes_through_input_elision(self):
        block = {
            "type": "tool_use",
            "id": "timed",
            "name": "Edit",
            "input": dict(BIG_INPUT),
            "started_at": STARTED,
            "ended_at": ENDED,
            "duration_ms": 192_000,
        }
        out = elide_tool_use_block(block)
        assert is_elided_input(out["input"])  # the heavy input is gone...
        assert out["started_at"] == STARTED  # ...and the timing is not
        assert out["ended_at"] == ENDED
        assert out["duration_ms"] == 192_000

    def test_tool_result_placeholder_keeps_its_end_stamp(self):
        out = elide_tool_result_block(_tool_result("timed", BIG, ended_at=ENDED))
        assert out["truncated"] is True and "content" not in out
        assert out["ended_at"] == ENDED

    def test_pre_d1_result_placeholder_is_byte_identical_to_before(self):
        """An untimed block must elide to the exact old placeholder — no empty ``ended_at``."""
        out = elide_tool_result_block(_tool_result("plain", BIG))
        assert "ended_at" not in out
        assert set(out) == {"type", "tool_use_id", "is_error", "truncated", "byte_size", "preview"}

    def test_timing_survives_the_shallow_endpoint(self):
        broker._conversation_turns = [
            ConversationTurn(
                id="a12",
                role="assistant",
                content="ran a long tool",
                parts=[
                    {
                        "type": "tool_use",
                        "id": "slow12",
                        "name": "Edit",
                        "input": dict(BIG_INPUT),
                        "started_at": STARTED,
                        "ended_at": ENDED,
                        "duration_ms": 192_000,
                    },
                    _tool_result("slow12", BIG, ended_at=ENDED),
                ],
            ),
        ]
        try:
            client = TestClient(app, raise_server_exceptions=False)
            data = client.get("/api/conversation/history", params={"detail": "shallow"}).json()
            parts = data["turns"][0]["parts"]
            call = next(p for p in parts if p.get("id") == "slow12")
            result = next(p for p in parts if p.get("tool_use_id") == "slow12")
            assert call["duration_ms"] == 192_000
            assert call["started_at"] == STARTED and call["ended_at"] == ENDED
            assert result["truncated"] is True and result["ended_at"] == ENDED
        finally:
            broker._conversation_turns = []
