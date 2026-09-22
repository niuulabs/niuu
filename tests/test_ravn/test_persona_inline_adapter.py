"""Unit tests for InlinePersonaAdapter (src/ravn/adapters/personas/inline.py).

Covers the alias-based lookup, exact-revision lookup, listing, and the
current-portable-by-id lookup including its ambiguity guard.
"""

from __future__ import annotations

import pytest

from ravn.adapters.personas.inline import InlinePersonaAdapter
from ravn.adapters.personas.loader import FilesystemPersonaAdapter, PersonaConfig
from ravn.domain.persona_document import portable_persona_from_config


def _persona(name: str = "reviewer", prompt: str = "Pinned prompt") -> PersonaConfig:
    return PersonaConfig(name=name, system_prompt_template=prompt)


def test_load_returns_none_for_unknown_alias() -> None:
    document = portable_persona_from_config(_persona())
    adapter = InlinePersonaAdapter(definitions={"review": document.to_dict()})

    assert adapter.load("missing-alias") is None


def test_load_raises_when_underlying_parse_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    document = portable_persona_from_config(_persona())
    adapter = InlinePersonaAdapter(definitions={"review": document.to_dict()})

    monkeypatch.setattr(FilesystemPersonaAdapter, "parse", staticmethod(lambda _text: None))

    with pytest.raises(
        ValueError,
        match=r"Portable persona definition for 'review' could not be parsed",
    ):
        adapter.load("review")


def test_load_portable_matches_exact_id_and_revision() -> None:
    document = portable_persona_from_config(_persona())
    adapter = InlinePersonaAdapter(definitions={"review": document.to_dict()})

    found = adapter.load_portable("reviewer", document.revision)

    assert found == document


def test_load_portable_returns_none_when_id_does_not_match() -> None:
    document = portable_persona_from_config(_persona())
    adapter = InlinePersonaAdapter(definitions={"review": document.to_dict()})

    assert adapter.load_portable("other-persona", document.revision) is None


def test_load_portable_returns_none_when_revision_does_not_match() -> None:
    document = portable_persona_from_config(_persona())
    adapter = InlinePersonaAdapter(definitions={"review": document.to_dict()})

    assert adapter.load_portable("reviewer", "content-doesnotexist") is None


def test_list_names_returns_sorted_aliases() -> None:
    review = portable_persona_from_config(_persona(name="reviewer"))
    triage = portable_persona_from_config(_persona(name="triage"))
    adapter = InlinePersonaAdapter(
        definitions={"zeta": review.to_dict(), "alpha": triage.to_dict()}
    )

    assert adapter.list_names() == ["alpha", "zeta"]


def test_load_current_portable_returns_single_match() -> None:
    document = portable_persona_from_config(_persona())
    adapter = InlinePersonaAdapter(definitions={"review": document.to_dict()})

    assert adapter.load_current_portable("reviewer") == document


def test_load_current_portable_returns_none_when_no_candidates() -> None:
    document = portable_persona_from_config(_persona())
    adapter = InlinePersonaAdapter(definitions={"review": document.to_dict()})

    assert adapter.load_current_portable("unknown-persona-id") is None


def test_load_current_portable_raises_on_multiple_revisions() -> None:
    first = portable_persona_from_config(_persona(prompt="Prompt one"))
    second = portable_persona_from_config(_persona(prompt="Prompt two"))
    adapter = InlinePersonaAdapter(
        definitions={"review-v1": first.to_dict(), "review-v2": second.to_dict()}
    )

    with pytest.raises(
        ValueError,
        match=r"Multiple inline revisions exist for persona 'reviewer'",
    ):
        adapter.load_current_portable("reviewer")
