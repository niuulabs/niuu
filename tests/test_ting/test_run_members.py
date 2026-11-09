"""A run's members, as the workflow snapshot gives them."""

from __future__ import annotations

from ting.domain.run_members import member_topics


def _snapshot(*stages: list[dict]) -> dict:
    return {
        "graph": {
            "nodes": [
                {"id": f"stage-{i}", "kind": "stage", "stageMembers": members}
                for i, members in enumerate(stages)
            ]
        }
    }


def test_a_members_topics_are_every_topic_its_stages_route_to_it() -> None:
    snapshot = _snapshot(
        [
            {
                "personaId": "coder",
                "consumesEventTypes": ["code.requested", "review.changes_requested"],
            }
        ],
        [{"personaId": "reviewer", "consumesEventTypes": ["code.changed"]}],
        [{"personaId": "coder", "consumesEventTypes": ["merge.approved", "code.requested"]}],
    )

    assert member_topics(snapshot, "coder") == (
        "code.requested",
        "merge.approved",
        "review.changes_requested",
    )
    assert member_topics(snapshot, "reviewer") == ("code.changed",)


def test_the_snake_case_spelling_counts_and_a_member_with_none_has_none() -> None:
    snapshot = _snapshot(
        [{"personaId": "auditor", "consumes_event_types": [" code.changed ", ""]}],
        [{"personaId": "scribe"}],
    )

    assert member_topics(snapshot, "auditor") == ("code.changed",)
    assert member_topics(snapshot, "scribe") == ()
