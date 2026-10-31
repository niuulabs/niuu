"""Tests for the Jira ADF → Markdown renderer."""

from __future__ import annotations

import pytest

from volundr.adapters.outbound.jira_adf import adf_to_markdown, text_to_adf


def _doc(*content: dict) -> dict:
    return {"type": "doc", "version": 1, "content": list(content)}


def _p(*inline: dict) -> dict:
    return {"type": "paragraph", "content": list(inline)}


def _t(text: str, *marks: dict) -> dict:
    node: dict = {"type": "text", "text": text}
    if marks:
        node["marks"] = list(marks)
    return node


def _li(*content: dict) -> dict:
    return {"type": "listItem", "content": list(content)}


class TestScalars:
    def test_none_is_no_body(self):
        assert adf_to_markdown(None) is None

    def test_plain_string_passes_through(self):
        assert adf_to_markdown("plain body") == "plain body"

    def test_empty_string_is_no_body(self):
        assert adf_to_markdown("") is None

    def test_empty_doc_is_no_body(self):
        assert adf_to_markdown(_doc()) is None

    def test_non_adf_value_raises(self):
        with pytest.raises(TypeError, match="not ADF: list"):
            adf_to_markdown(["not", "adf"])


class TestBlocks:
    def test_paragraphs_and_headings(self):
        doc = _doc(
            {"type": "heading", "attrs": {"level": 3}, "content": [_t("Scope")]},
            _p(_t("First line"), {"type": "hardBreak"}, _t("second line")),
            {"type": "rule"},
        )
        assert adf_to_markdown(doc) == "### Scope\n\nFirst line\nsecond line\n\n---"

    def test_heading_defaults_to_level_one(self):
        assert adf_to_markdown(_doc({"type": "heading", "content": [_t("T")]})) == "# T"

    def test_nested_bullet_and_ordered_lists(self):
        doc = _doc(
            {
                "type": "orderedList",
                "attrs": {"order": 3},
                "content": [
                    _li(
                        _p(_t("outer")),
                        {"type": "bulletList", "content": [_li(_p(_t("inner")))]},
                    ),
                    _li(_p(_t("next"))),
                ],
            }
        )
        assert adf_to_markdown(doc) == "3. outer\n   - inner\n4. next"

    def test_task_list_marks_done_items(self):
        doc = _doc(
            {
                "type": "taskList",
                "content": [
                    {"type": "taskItem", "attrs": {"state": "DONE"}, "content": [_t("shipped")]},
                    {"type": "taskItem", "attrs": {"state": "TODO"}, "content": [_t("pending")]},
                    {
                        "type": "taskItem",
                        "attrs": {"state": "TODO"},
                        "content": [_p(_t("a")), _p(_t("b"))],
                    },
                ],
            }
        )
        assert adf_to_markdown(doc) == "- [x] shipped\n- [ ] pending\n- [ ] a\n      b"

    def test_decision_list(self):
        doc = _doc(
            {
                "type": "decisionList",
                "content": [{"type": "decisionItem", "content": [_t("use postgres")]}],
            }
        )
        assert adf_to_markdown(doc) == "- use postgres"

    def test_code_block_keeps_language(self):
        doc = _doc(
            {"type": "codeBlock", "attrs": {"language": "python"}, "content": [_t("x = 1")]},
            {"type": "codeBlock", "content": [_t("plain")]},
        )
        assert adf_to_markdown(doc) == "```python\nx = 1\n```\n\n```\nplain\n```"

    def test_blockquote_and_panel(self):
        doc = _doc(
            {"type": "blockquote", "content": [_p(_t("quoted")), _p(_t("more"))]},
            {"type": "panel", "attrs": {"panelType": "warning"}, "content": [_p(_t("careful"))]},
            {"type": "panel", "content": [_p(_t("bare"))]},
        )
        assert adf_to_markdown(doc) == ("> quoted\n>\n> more\n\n> **Warning:** careful\n\n> bare")

    def test_table(self):
        def cell(kind: str, text: str) -> dict:
            return {"type": kind, "content": [_p(_t(text))]}

        doc = _doc(
            {
                "type": "table",
                "content": [
                    {
                        "type": "tableRow",
                        "content": [cell("tableHeader", "Key"), cell("tableHeader", "Value")],
                    },
                    {"type": "tableRow", "content": [cell("tableCell", "a|b")]},
                ],
            }
        )
        assert adf_to_markdown(doc) == "| Key | Value |\n|---|---|\n| a\\|b |  |"

    def test_empty_table(self):
        assert adf_to_markdown(_doc({"type": "table", "content": []})) is None

    def test_expand_with_and_without_title(self):
        doc = _doc(
            {"type": "expand", "attrs": {"title": "Details"}, "content": [_p(_t("hidden"))]},
            {"type": "nestedExpand", "content": [_p(_t("also"))]},
        )
        assert adf_to_markdown(doc) == "**Details**\n\nhidden\n\nalso"

    def test_media(self):
        doc = _doc(
            {
                "type": "mediaSingle",
                "content": [
                    {"type": "media", "attrs": {"alt": "diagram.png", "url": "https://x/d.png"}}
                ],
            },
            {
                "type": "mediaGroup",
                "content": [
                    {"type": "media", "attrs": {"filename": "spec.pdf"}},
                    {"type": "media", "attrs": {}},
                ],
            },
            {"type": "media", "attrs": {"id": "abc"}},
        )
        assert adf_to_markdown(doc) == (
            "[attachment: diagram.png](https://x/d.png)\n\n"
            "[attachment: spec.pdf]\n\n"
            "[attachment: abc]"
        )

    def test_unknown_block_keeps_its_text(self):
        doc = _doc(
            {
                "type": "layoutSection",
                "content": [{"type": "layoutColumn", "content": [_p(_t("col"))]}],
            },
            {"type": "futureInline", "content": [_t("inline kept")]},
            {"type": "blockCard", "attrs": {"url": "https://example.com"}},
        )
        assert adf_to_markdown(doc) == "col\n\ninline kept\n\n<https://example.com>"


class TestInline:
    def test_marks(self):
        doc = _doc(
            _p(
                _t("bold", {"type": "strong"}),
                _t(" "),
                _t("it", {"type": "em"}, {"type": "underline"}),
                _t(" "),
                _t("gone", {"type": "strike"}),
                _t(" "),
                _t("cmd", {"type": "code"}),
                _t(" "),
                _t("docs", {"type": "link", "attrs": {"href": "https://d"}}, {"type": "strong"}),
                _t("", {"type": "strong"}),
            )
        )
        assert adf_to_markdown(doc) == "**bold** *it* ~~gone~~ `cmd` [**docs**](https://d)"

    def test_inline_nodes(self):
        doc = _doc(
            _p(
                {"type": "mention", "attrs": {"id": "u1", "text": "@Ada"}},
                _t(" "),
                {"type": "mention", "attrs": {"id": "u2"}},
                _t(" "),
                {"type": "emoji", "attrs": {"shortName": ":tada:"}},
                {"type": "emoji", "attrs": {"shortName": ":x:", "text": "❌"}},
                _t(" "),
                {"type": "inlineCard", "attrs": {"url": "https://card"}},
                {"type": "inlineCard", "attrs": {}},
                _t(" "),
                {"type": "status", "attrs": {"text": "BLOCKED"}},
                _t(" "),
                {"type": "date", "attrs": {"timestamp": "1700000000000"}},
                _t(" "),
                {"type": "placeholder", "attrs": {"text": "type here"}},
                {"type": "unknownInline"},
                {"type": "wrapper", "content": [_t("!")]},
            )
        )
        assert adf_to_markdown(doc) == (
            "@Ada @u2 :tada:❌ <https://card> [BLOCKED] 1700000000000 type here!"
        )


class TestTextToAdf:
    def test_one_paragraph_per_line_round_trips(self):
        text = "## Criteria\n\n- [ ] works"
        doc = text_to_adf(text)

        assert doc["type"] == "doc"
        assert len(doc["content"]) == 3
        assert adf_to_markdown(doc) == text

    def test_empty_text_is_one_empty_paragraph(self):
        assert text_to_adf("") == {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": []}],
        }
