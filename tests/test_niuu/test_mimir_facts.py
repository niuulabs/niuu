"""Typed-fact domain rules: trust, validity, selection and packing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from niuu.domain.mimir_facts import (
    AuthorKind,
    Fact,
    FactAuthor,
    FactCaller,
    FactDraft,
    FactKind,
    FactProvenance,
    FactValidationError,
    OmissionReason,
    Retirement,
    SharedOrigin,
    TrustError,
    TrustLevel,
    derive_trust,
    pack_facts,
    rank_facts,
    received_trust,
    render_fact_line,
    require_may_override,
    select_facts,
    share_bundle,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def _caller(kind: AuthorKind, ident: str = "x") -> FactCaller:
    return FactCaller(FactAuthor(kind, ident))


def _fact(
    fact_id: str = "1@a",
    *,
    trust: TrustLevel = TrustLevel.AGENT,
    statement: str = "a claim",
    valid_from: datetime = NOW - timedelta(days=10),
    valid_until: datetime | None = None,
    retired_at: datetime | None = None,
    origin: SharedOrigin | None = None,
    recorded_at: datetime | None = None,
) -> Fact:
    return Fact(
        id=fact_id,
        subject="a",
        statement=statement,
        kind=FactKind.FACT,
        trust=trust,
        provenance=FactProvenance(
            author=FactAuthor(AuthorKind.AGENT, "regin"), evidence=("page.md",), origin=origin
        ),
        recorded_at=recorded_at or valid_from,
        valid_from=valid_from,
        valid_until=valid_until,
        retired=Retirement(at=retired_at, by="person:j", why="wrong") if retired_at else None,
    )


class TestTrustLevels:
    def test_order_is_person_over_operator_over_tool_over_agent(self) -> None:
        assert TrustLevel.PERSON > TrustLevel.OPERATOR > TrustLevel.TOOL > TrustLevel.AGENT

    @pytest.mark.parametrize("value", ["person", "PERSON", " Person ", 4, TrustLevel.PERSON])
    def test_parse_accepts_names_and_numbers(self, value) -> None:
        assert TrustLevel.parse(value) is TrustLevel.PERSON

    def test_parse_rejects_unknown_names_naming_the_choices(self) -> None:
        with pytest.raises(FactValidationError, match="agent, tool, operator, person"):
            TrustLevel.parse("god")

    def test_level_follows_the_author_kind_and_nothing_else(self) -> None:
        assert _caller(AuthorKind.AGENT, "regin").trust is TrustLevel.AGENT
        assert _caller(AuthorKind.TOOL).trust is TrustLevel.TOOL
        assert _caller(AuthorKind.OPERATOR).trust is TrustLevel.OPERATOR
        assert _caller(AuthorKind.PERSON).trust is TrustLevel.PERSON
        assert _caller(AuthorKind.EXTERNAL).trust is TrustLevel.AGENT

    def test_a_resident_never_holds_the_top_level_whatever_it_calls_itself(self) -> None:
        # A name that claims confirmation is still an agent's name.
        caller = _caller(AuthorKind.AGENT, "person:jozef-confirmed")
        assert caller.trust is TrustLevel.AGENT

    def test_a_draft_has_no_way_to_name_a_trust_level(self) -> None:
        draft = FactDraft("a", "claim", ("e",))
        assert not hasattr(draft, "trust")
        with pytest.raises(TypeError):
            FactDraft("a", "claim", ("e",), trust=TrustLevel.PERSON)  # type: ignore[call-arg]


class TestDerivedAndReceivedTrust:
    def test_derived_fact_takes_the_lowest_input_level(self) -> None:
        inputs = [_fact("1@a", trust=TrustLevel.PERSON), _fact("2@a", trust=TrustLevel.TOOL)]
        assert derive_trust(_caller(AuthorKind.PERSON), inputs) is TrustLevel.TOOL

    def test_derived_fact_is_capped_by_its_own_caller(self) -> None:
        inputs = [_fact(trust=TrustLevel.PERSON)]
        assert derive_trust(_caller(AuthorKind.AGENT), inputs) is TrustLevel.AGENT

    def test_without_inputs_the_caller_level_stands(self) -> None:
        assert derive_trust(_caller(AuthorKind.OPERATOR), []) is TrustLevel.OPERATOR

    def test_received_fact_never_exceeds_the_importer(self) -> None:
        assert received_trust(_caller(AuthorKind.AGENT), TrustLevel.PERSON) is TrustLevel.AGENT
        assert received_trust(_caller(AuthorKind.PERSON), TrustLevel.TOOL) is TrustLevel.TOOL


class TestOverride:
    def test_agent_may_not_override_a_person_confirmed_fact(self) -> None:
        with pytest.raises(TrustError, match="may not retire fact 1@a"):
            require_may_override(
                _caller(AuthorKind.AGENT, "regin"), _fact(trust=TrustLevel.PERSON), "retire"
            )

    def test_equal_or_higher_levels_may(self) -> None:
        require_may_override(_caller(AuthorKind.TOOL), _fact(trust=TrustLevel.TOOL), "replace")
        require_may_override(_caller(AuthorKind.PERSON), _fact(trust=TrustLevel.TOOL), "replace")


class TestDraft:
    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"subject": " "}, "subject"),
            ({"statement": ""}, "statement"),
            ({"evidence": ()}, "provenance"),
            ({"evidence": ("ok", " ")}, "provenance"),
            ({"valid_from": datetime(2026, 1, 1)}, "timezone"),
            ({"valid_until": datetime(2026, 1, 1)}, "timezone"),
            ({"valid_from": NOW, "valid_until": NOW}, "later than"),
            ({"kind": FactKind.IDEA}, "read-only"),
        ],
    )
    def test_invalid_drafts_are_refused(self, kwargs, match) -> None:
        base = {"subject": "a", "statement": "s", "evidence": ("e",)}
        with pytest.raises(FactValidationError, match=match):
            FactDraft(**{**base, **kwargs})


class TestAuthor:
    def test_ref_round_trips(self) -> None:
        author = FactAuthor(AuthorKind.AGENT, "regin")
        assert FactAuthor.parse(author.ref) == author

    @pytest.mark.parametrize("ref", ["regin", "wizard:x"])
    def test_bad_refs_are_refused(self, ref) -> None:
        with pytest.raises(FactValidationError):
            FactAuthor.parse(ref)

    def test_blank_id_is_refused(self) -> None:
        with pytest.raises(FactValidationError):
            FactAuthor(AuthorKind.AGENT, " ")


class TestValidity:
    def test_fact_is_valid_inside_its_window(self) -> None:
        assert _fact(valid_until=NOW + timedelta(days=1)).valid_at(NOW)

    def test_fact_is_not_valid_before_it_starts(self) -> None:
        assert not _fact().valid_at(NOW - timedelta(days=11))

    def test_expired_fact_is_not_valid_after_its_end(self) -> None:
        assert not _fact(valid_until=NOW - timedelta(seconds=1)).valid_at(NOW)

    def test_retirement_ends_validity_but_not_the_past(self) -> None:
        retired = _fact(retired_at=NOW - timedelta(days=2))
        assert not retired.valid_at(NOW)
        assert retired.valid_at(NOW - timedelta(days=5))

    def test_earlier_of_expiry_and_retirement_ends_it(self) -> None:
        fact = _fact(valid_until=NOW + timedelta(days=5), retired_at=NOW - timedelta(days=1))
        assert fact.ends_at == NOW - timedelta(days=1)


class TestSelect:
    def test_as_of_drops_expired_facts_and_says_why(self) -> None:
        live = _fact("1@a")
        gone = _fact("2@a", valid_until=NOW - timedelta(days=1))
        kept, omitted = select_facts([live, gone], min_trust=TrustLevel.AGENT, as_of=NOW)
        assert kept == [live]
        assert omitted[0].reason is OmissionReason.NOT_VALID_AT
        assert omitted[0].fact_ids == ("2@a",)

    def test_as_of_in_the_past_returns_what_was_valid_then(self) -> None:
        later_retired = _fact("1@a", retired_at=NOW - timedelta(days=1))
        kept, omitted = select_facts(
            [later_retired], min_trust=TrustLevel.AGENT, as_of=NOW - timedelta(days=3)
        )
        assert kept == [later_retired]
        assert omitted == []

    def test_min_trust_drops_weaker_facts_and_says_why(self) -> None:
        weak, strong = _fact("1@a"), _fact("2@a", trust=TrustLevel.TOOL)
        kept, omitted = select_facts([weak, strong], min_trust=TrustLevel.TOOL, as_of=NOW)
        assert kept == [strong]
        assert omitted[0].reason is OmissionReason.BELOW_MIN_TRUST
        assert omitted[0].count == 1


class TestRankAndPack:
    def test_task_relevance_leads_then_trust_then_recency(self) -> None:
        irrelevant_person = _fact("1@a", trust=TrustLevel.PERSON, statement="weather is mild")
        relevant_agent = _fact("2@a", statement="deploy uses helm")
        relevant_tool_old = _fact("3@a", trust=TrustLevel.TOOL, statement="deploy uses flux")
        ranked = rank_facts([irrelevant_person, relevant_agent, relevant_tool_old], "deploy")
        assert [f.id for f in ranked] == ["3@a", "2@a", "1@a"]

    def test_without_a_task_trust_leads(self) -> None:
        facts = [_fact("1@a"), _fact("2@a", trust=TrustLevel.PERSON)]
        assert rank_facts(facts, None)[0].id == "2@a"

    def test_budget_keeps_the_ranked_prefix_and_reports_the_rest(self) -> None:
        facts = [
            _fact("1@a", trust=TrustLevel.PERSON, statement="x" * 40),
            _fact("2@a", statement="y" * 40),
        ]
        one_line = len(render_fact_line(facts[0]))
        kept, omitted, used = pack_facts(
            facts, task=None, budget_tokens=one_line // 4 + 1, chars_per_token=4
        )
        assert [f.id for f in kept] == ["1@a"]
        assert omitted[0].reason is OmissionReason.BUDGET
        assert omitted[0].fact_ids == ("2@a",)
        assert "token budget" in omitted[0].detail
        assert used <= one_line // 4 + 1

    def test_no_budget_keeps_everything(self) -> None:
        kept, omitted, _ = pack_facts([_fact()], task=None, budget_tokens=None, chars_per_token=4)
        assert len(kept) == 1 and omitted == []


class TestSharing:
    def test_bundle_carries_the_original_author_and_trust(self) -> None:
        bundle = share_bundle(_fact(trust=TrustLevel.TOOL), source="mimir-a")
        assert bundle.author == FactAuthor(AuthorKind.AGENT, "regin")
        assert bundle.trust is TrustLevel.TOOL
        assert bundle.source == "mimir-a"

    def test_reshared_fact_keeps_its_first_origin_not_the_relay(self) -> None:
        origin = SharedOrigin(
            author=FactAuthor(AuthorKind.AGENT, "sigrun"),
            claimed_trust=TrustLevel.TOOL,
            fact_id="9@a",
            source="mimir-origin",
            evidence=("src_1",),
        )
        relayed = _fact("5@a", origin=origin)
        bundle = share_bundle(relayed, source="mimir-relay")
        assert bundle.author.id == "sigrun"
        assert bundle.fact_id == "9@a"
        assert bundle.source == "mimir-origin"
        assert bundle.evidence == ("src_1",)
