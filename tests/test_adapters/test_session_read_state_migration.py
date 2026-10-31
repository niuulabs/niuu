"""The inbox schema migrations are uniquely numbered and keep their alias."""

from pathlib import Path


def test_unique_sequences_and_immutable_coordination_alias():
    import hashlib
    import json

    root = Path(__file__).resolve().parents[2]
    paths = list((root / "migrations").glob("*.up.sql"))
    numbers = [path.name.split("_", 1)[0] for path in paths]
    assert len(numbers) == len(set(numbers))
    name = "000074_session_coordination_revision.up.sql"
    alias = json.loads((root / "migrations/lineage-aliases.json").read_text())[name][0]
    assert alias["filename"] == "000067_session_coordination_revision.up.sql"
    assert alias["sha256"] == hashlib.sha256((root / "migrations" / name).read_bytes()).hexdigest()
