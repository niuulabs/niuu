"""The question standard on agent notifications (LEX-1671, LEX-1597 point 3)."""

from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from niuu.domain.notifications import NotificationAsk, NotificationDraft, NotificationKind
from niuu.forge_mcp.question_lint import lint_question
from niuu.question_standard import QUESTION_STANDARD
from tests.test_niuu.test_forge_mcp_tools import _feed_toolbox, _toolbox

CHOICE = {
    "id": "database",
    "kind": "question",
    "text": "Which database for the cache?",
    "options": [
        {"label": "Postgres", "description": "Already deployed", "recommended": True},
        {"label": "SQLite"},
    ],
    "default": {"option": "Postgres", "after": "PT4H"},
}


def _ask(**item) -> dict:
    return {"items": [{**CHOICE, **item}]}


def _draft(kind: str, title: str, body: str = "", ask: dict | None = None) -> NotificationDraft:
    extra = {"question": True, "ask": ask} if ask is not None else {}
    return NotificationDraft.model_validate({"kind": kind, "title": title, "body": body, **extra})


class TestAskFields:
    def test_recommended_default_and_deadline_round_trip(self) -> None:
        ask = NotificationAsk.model_validate(_ask(deadline="2026-10-31T12:00:00+01:00"))
        item = ask.wire()["items"][0]
        assert item["options"][0]["recommended"] is True
        assert "recommended" not in item["options"][1]  # omitted stays omitted
        assert item["default"] == {"option": "Postgres", "after": "PT4H"}
        assert item["deadline"] == "2026-10-31T12:00:00+01:00"
        assert ask.items[0].recommended_option.label == "Postgres"

    def test_legacy_recommended_suffix_still_counts(self) -> None:
        ask = NotificationAsk.model_validate(
            {
                "items": [
                    {
                        "id": "db",
                        "kind": "question",
                        "text": "Which?",
                        "options": [{"label": "Postgres (Recommended)"}, {"label": "SQLite"}],
                    }
                ]
            }
        )
        assert ask.items[0].recommended_option.label == "Postgres (Recommended)"
        assert ask.wire()["items"][0]["options"][0] == {"label": "Postgres (Recommended)"}

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            (
                {
                    "options": [
                        {"label": "A", "recommended": True},
                        {"label": "B", "recommended": True},
                    ]
                },
                "at most one option",
            ),
            (
                {"options": [{"label": "A", "recommended": True}, {"label": "B (Recommended)"}]},
                "at most one option",
            ),
            ({"default": {"option": "MySQL", "after": "PT1H"}}, "default.option"),
            ({"default": {"option": "Postgres", "after": "tomorrow"}}, "default.after"),
            ({"default": {"option": "Postgres", "after": "2026-10-31T12:00:00"}}, "timezone"),
            ({"default": {"option": "Postgres"}}, "after"),
            ({"deadline": "next week"}, "deadline"),
            ({"options": [{"label": "A", "recommended": None}]}, "recommended"),
        ],
    )
    def test_invalid_fields_are_refused(self, change: dict, message: str) -> None:
        with pytest.raises(ValidationError, match=message):
            NotificationAsk.model_validate(_ask(**change))

    @pytest.mark.parametrize(
        "after", ["PT4H", "P1D", "P1DT12H", "PT30M", "PT90S", "2026-10-31T12:00:00Z"]
    )
    def test_default_after_accepts_durations_and_timestamps(self, after: str) -> None:
        NotificationAsk.model_validate(_ask(default={"option": "none", "after": after}))

    def test_action_items_default_to_none(self) -> None:
        action = {"id": "access", "kind": "action", "text": "Grant access."}
        NotificationAsk.model_validate(
            {"items": [{**action, "default": {"option": "none", "after": "P1D"}}]}
        )
        with pytest.raises(ValidationError, match="default.option"):
            NotificationAsk.model_validate(
                {"items": [{**action, "default": {"option": "Done", "after": "P1D"}}]}
            )


class TestLint:
    @pytest.mark.parametrize("kind", ["decision", "attention"])
    def test_without_a_question_is_downgraded_to_info(self, kind: str) -> None:
        linted = lint_question(_draft(kind, "Switched the cache to Postgres"))
        assert linted.draft.kind is NotificationKind.INFO
        assert linted.downgraded_from is NotificationKind(kind)
        assert QUESTION_STANDARD in linted.warnings[0]
        assert f"not {kind}" in linted.warnings[0]

    @pytest.mark.parametrize(
        ("title", "body"),
        [
            ("Postgres or SQLite for the cache?", ""),
            ("Cache database", "- Postgres (recommended)\n- SQLite"),
            ("Cache database", "1. Postgres\n2. SQLite"),
            ("Cache database", "A) Postgres\nB) SQLite"),
        ],
    )
    def test_free_text_questions_pass(self, title: str, body: str) -> None:
        linted = lint_question(_draft("decision", title, body))
        assert linted.draft.kind is NotificationKind.DECISION
        assert linted.warnings == ()

    def test_one_listed_line_is_not_a_list_of_options(self) -> None:
        linted = lint_question(_draft("attention", "Deploy blocked", "- waiting on CI"))
        assert linted.draft.kind is NotificationKind.INFO

    def test_complete_ask_passes_silently(self) -> None:
        linted = lint_question(_draft("decision", "Cache database", ask=_ask()))
        assert linted.warnings == () and linted.downgraded_from is None

    def test_ask_without_recommendation_or_default_is_accepted_with_a_warning(self) -> None:
        ask = _ask(options=[{"label": "Postgres"}, {"label": "SQLite"}], default=None)
        ask["items"][0].pop("default")
        linted = lint_question(_draft("decision", "Cache database", ask=ask))
        assert linted.draft.kind is NotificationKind.DECISION
        assert linted.downgraded_from is None
        (warning,) = linted.warnings
        assert "no option marked recommended=true in database" in warning
        assert "no default {option, after} in database" in warning
        assert QUESTION_STANDARD in warning

    @pytest.mark.parametrize("kind", ["milestone", "info", "error"])
    def test_other_kinds_are_untouched(self, kind: str) -> None:
        linted = lint_question(_draft(kind, "Shipped"))
        assert linted.draft.kind is NotificationKind(kind) and linted.warnings == ()


class TestNotifyTool:
    async def test_transcript_notify_records_info_and_says_why(self) -> None:
        toolbox, _, host = _toolbox()
        result = await toolbox.call("notify", {"kind": "decision", "title": "Chose Postgres"})
        assert not result.is_error
        assert host.drafts[-1].kind is NotificationKind.INFO
        assert result.structured["kind"] == "info"
        assert result.structured["requested_kind"] == "decision"
        assert QUESTION_STANDARD in result.structured["warnings"][0]

    async def test_feed_notify_records_info_and_says_why(self) -> None:
        toolbox, client = _feed_toolbox()
        result = await toolbox.call(
            "notify", {"kind": "attention", "title": "Heads up", "idempotency_key": "k-2"}
        )
        _, (_, draft, _, _) = client.calls[-1]
        assert draft.kind is NotificationKind.INFO
        assert result.structured["state"] == "committed"
        assert result.structured["requested_kind"] == "attention"

    async def test_structured_question_goes_through_unchanged(self) -> None:
        toolbox, _, host = _toolbox()
        result = await toolbox.call(
            "notify",
            {"kind": "decision", "title": "Cache database", "question": True, "ask": _ask()},
        )
        assert "warnings" not in result.structured
        sent = host.drafts[-1]
        assert sent.kind is NotificationKind.DECISION
        assert sent.ask.wire()["items"][0]["default"] == {"option": "Postgres", "after": "PT4H"}

    def test_schema_and_description_carry_the_standard(self) -> None:
        toolbox, _, _ = _toolbox()
        notify = next(tool for tool in toolbox.list_tools() if tool["name"] == "notify")
        schema = notify["inputSchema"]["properties"]
        item = schema["ask"]["properties"]["items"]["items"]["properties"]
        assert item["options"]["items"]["properties"]["recommended"]["type"] == "boolean"
        assert item["default"]["required"] == ["option", "after"]
        assert "deadline" in item
        assert "decision = the user must choose" in schema["kind"]["description"]
        assert "significant choice you made" not in schema["kind"]["description"]
        assert QUESTION_STANDARD in notify["description"]


class TestInstructions:
    def test_every_surface_states_the_standard(self) -> None:
        from skuld.forge_mcp.skill import forge_notify_skill_markdown
        from skuld.forge_mcp.stdio import SERVER_INSTRUCTIONS
        from skuld.transports.session_tools import capability_instructions
        from volundr.adapters.inbound.rest_forge_mcp import INSTRUCTIONS

        assert QUESTION_STANDARD in SERVER_INSTRUCTIONS
        assert QUESTION_STANDARD in INSTRUCTIONS
        assert QUESTION_STANDARD in capability_instructions(forge_mcp_server="forge")
        assert QUESTION_STANDARD in forge_notify_skill_markdown("forge")

    def test_stdio_server_still_starts_without_pydantic(self) -> None:
        code = (
            "import sys, skuld.forge_mcp.stdio as s; "
            "assert s.SERVER_INSTRUCTIONS; "
            "print('pydantic' in sys.modules)"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        assert out.stdout.strip() == "False"
