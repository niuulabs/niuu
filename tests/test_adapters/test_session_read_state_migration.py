"""The chart and local migrator ship exactly the same additive inbox schema."""

from pathlib import Path

import pytest


@pytest.mark.parametrize("direction", ["up", "down"])
def test_local_and_helm_migrations_match_inside_enabled_guard(direction):
    root = Path(__file__).resolve().parents[2]
    name = f"000067_session_read_state.{direction}.sql"
    chart = (root / "charts/volundr/templates/migrations-configmap.yaml").read_text()
    start = chart.index(f"  {name}: |\n") + len(f"  {name}: |\n")
    lines = []
    for line in chart[start:].splitlines():
        if line and not line.startswith("    "):
            break
        lines.append(line[4:])
    assert "\n".join(lines).strip() == (root / "migrations" / name).read_text().strip()
    assert start < chart.rindex("{{- end }}")


def test_unique_sequences_and_immutable_coordination_alias():
    import hashlib
    import json

    root = Path(__file__).resolve().parents[2]
    paths = list((root / "migrations").glob("*.up.sql"))
    numbers = [path.name.split("_", 1)[0] for path in paths]
    assert len(numbers) == len(set(numbers))
    name = "000068_session_coordination_revision.up.sql"
    alias = json.loads((root / "migrations/lineage-aliases.json").read_text())[name][0]
    assert alias["filename"] == "000067_session_coordination_revision.up.sql"
    assert alias["sha256"] == hashlib.sha256((root / "migrations" / name).read_bytes()).hexdigest()
    for path in paths:
        assert path.read_bytes() == (root / "src/cli/migrations/volundr" / path.name).read_bytes()
