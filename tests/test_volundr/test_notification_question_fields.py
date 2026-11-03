"""REST notifications expose an ask's recommended option, default and deadline (LEX-1671)."""

from __future__ import annotations

from tests.support.forge_session import PREFIX, build_forge_app

ASK = {
    "items": [
        {
            "id": "database",
            "kind": "question",
            "text": "Which database for the cache?",
            "options": [
                {"label": "Postgres", "recommended": True},
                {"label": "SQLite"},
            ],
            "default": {"option": "Postgres", "after": "PT4H"},
            "deadline": "2026-10-31T12:00:00Z",
        }
    ]
}


def _submit(forge, session_id, ask, key: str):
    return forge.client.post(
        f"{PREFIX}/sessions/{session_id}/notifications",
        json={
            "kind": "decision",
            "title": "Postgres or SQLite for the cache?",
            "question": True,
            "ask": ask,
            "idempotency_key": key,
        },
    )


async def test_submit_and_list_carry_the_new_fields() -> None:
    # The feed serializes rows with the same NotificationRecord.wire() as this list.
    forge = build_forge_app()
    session = await forge.session()

    created = _submit(forge, session.id, ASK, "lex1671-1")
    listed = forge.client.get(f"{PREFIX}/sessions/{session.id}/notifications")

    assert created.status_code == 201, created.text
    assert created.json()["ask"] == ASK
    rows = listed.json()
    rows = rows.get("items", rows) if isinstance(rows, dict) else rows
    assert rows[0]["ask"] == ASK


async def test_legacy_recommended_suffix_is_still_accepted() -> None:
    forge = build_forge_app()
    session = await forge.session()
    legacy = {
        "items": [
            {
                "id": "db",
                "kind": "question",
                "text": "Which?",
                "options": [{"label": "Postgres (Recommended)"}, {"label": "SQLite"}],
            }
        ]
    }

    created = _submit(forge, session.id, legacy, "lex1671-2")

    assert created.status_code == 201, created.text
    assert created.json()["ask"] == legacy


async def test_two_recommended_options_are_refused() -> None:
    forge = build_forge_app()
    session = await forge.session()
    bad = {
        "items": [
            {
                **ASK["items"][0],
                "options": [
                    {"label": "Postgres", "recommended": True},
                    {"label": "SQLite", "recommended": True},
                ],
            }
        ]
    }

    response = _submit(forge, session.id, bad, "lex1671-3")

    assert response.status_code == 422
    assert "at most one option" in response.text
