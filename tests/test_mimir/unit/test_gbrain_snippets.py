"""Search hits read as plain text, whatever the chunk gbrain matched holds."""

from __future__ import annotations

import json

from mimir.backends.gbrain_snippets import readable


class TestPages:
    def test_markdown_markup_is_removed_and_the_words_kept(self) -> None:
        chunk = (
            "### Key Facts\n- Ines Moreau owns the **booking cut-over**.\n"
            "- Relationships - [[ines-moreau|Ines]] — rel: leads — see [the plan](plan.md)."
        )
        assert readable(chunk) == (
            "Key Facts Ines Moreau owns the booking cut-over. "
            "Relationships - Ines — rel: leads — see the plan."
        )

    def test_a_timeline_rows_sealed_provenance_is_not_shown(self) -> None:
        seal = json.dumps({"a": "operator:jozef", "ev": ["source:src_1"], "w": "x{y}"})
        row = f"- **2026-11-03** | mt1.L8QFcoSzGTgAWQ2s2-pcYA.{seal}\n  Took it over."
        chunk = f"## Timeline\n{row}"
        assert readable(chunk) == "Timeline 2026-11-03 | Took it over."

    def test_a_seal_cut_off_by_the_chunk_takes_the_rest_with_it(self) -> None:
        chunk = 'Owner set. | mt1.abc_DEF-1.{"a":"operator:jozef","ev":["source:src_'
        assert readable(chunk) == "Owner set. |"

    def test_front_matter_and_the_page_seal_are_not_shown(self) -> None:
        chunk = "---\ntitle: Ines\nniuu_attribution: mp1.xyz.{}\n---\n\nInes owns it."
        assert readable(chunk) == "Ines owns it."

    def test_the_timeline_marker_comment_is_not_shown(self) -> None:
        assert readable("Truth.\n<!-- timeline -->\n- **2026-01-01** | Row") == (
            "Truth. 2026-01-01 | Row"
        )

    def test_nothing_is_shortened(self) -> None:
        words = " ".join(f"word{i}" for i in range(2000))
        assert readable(words) == words


class TestSources:
    def _record(self, content: str) -> str:
        body = {"source_id": "src_1", "title": "LIN-482", "kind": "document", "content": content}
        return f"```json\n{json.dumps(body)}\n```"

    def test_a_whole_record_reads_as_its_content(self) -> None:
        chunk = self._record("Owner: Ines Moreau.\nCut-over window: weekend of 2026-12-05.")
        assert readable(chunk, source=True) == (
            "Owner: Ines Moreau. Cut-over window: weekend of 2026-12-05."
        )

    def test_html_content_reads_as_its_text(self) -> None:
        chunk = self._record(
            '<div class="x"><h3>Theme</h3><p>Use <code>logo</code> &amp; '
            '<abbr title="Markedly">MyST</abbr>.</p><svg><path d="M1 2"/></svg></div>'
        )
        assert readable(chunk, source=True) == "Theme Use logo & MyST ."

    def test_a_chunk_that_starts_inside_the_content_and_inside_a_tag(self) -> None:
        chunk = (
            '9.06 0 0 1 1.5.124m7.5 3.375\\"></path></svg></button><p>Simple site'
            ' for articles.</p>", "origin_url": "https://example.org"}\n```'
        )
        assert readable(chunk, source=True) == "Simple site for articles."

    def test_a_chunk_cut_inside_an_escape_drops_the_broken_escape(self) -> None:
        assert readable('{"content": "caf\\u00e9 and more \\u00', source=True) == "café and more"

    def test_a_chunk_of_the_record_without_content_has_nothing_to_read(self) -> None:
        assert readable('```json\n{"source_id": "src_1", "title": "x"', source=True) == ""
