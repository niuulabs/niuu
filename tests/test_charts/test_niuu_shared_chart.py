"""Tests for the niuu-shared Helm chart templates."""

import re
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent.parent
CHART_DIR = REPO_ROOT / "charts" / "niuu-shared"
MIGRATIONS_DIR = REPO_ROOT / "migrations"


def _migration_blocks(template: str) -> dict[str, str]:
    pattern = re.compile(r"^  (\d+_[^:]+\.(?:up|down)\.sql): \|\n", re.MULTILINE)
    matches = list(pattern.finditer(template))
    template_end = template.index("{{- end }}")
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else template_end
        lines = template[start:end].splitlines()
        blocks[name] = (
            "\n".join(line[4:] if line.startswith("    ") else "" for line in lines).rstrip()
            + "\n"
        )
    return blocks


def test_embeds_valkyrie_history_migration_in_shared_db() -> None:
    template = (CHART_DIR / "templates" / "migrations-configmap.yaml").read_text()
    blocks = _migration_blocks(template)

    for filename in (
        "000049_valkyrie_history.up.sql",
        "000049_valkyrie_history.down.sql",
    ):
        assert blocks[filename] == (MIGRATIONS_DIR / filename).read_text().rstrip() + "\n"
