"""Delivery records are Mímir pages, never files committed to the delivered repo.

The coder, reviewer, closer and publisher personas used to write
``deliveries/{slug}/*.md`` into the repository and commit them, so every
delivery's paperwork landed in the product (and, for an infra repo, on `main`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

PERSONAS = Path(__file__).resolve().parents[2] / "src" / "ravn" / "personas"


def _persona(name: str) -> dict:
    return yaml.safe_load((PERSONAS / f"{name}.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["coder", "reviewer", "closer", "publisher"])
def test_delivery_personas_keep_records_in_mimir(name: str) -> None:
    persona = _persona(name)
    prompt = " ".join(persona["system_prompt_template"].split())

    assert {"mimir_read", "mimir_write"} <= set(persona["allowed_tools"])
    assert "Delivery records are Mímir pages, never repository files" in prompt
    assert "Never create or commit a `deliveries/` directory in the repository" in prompt


@pytest.mark.parametrize("name", ["coder", "reviewer", "closer", "publisher"])
def test_no_delivery_record_is_written_as_a_repository_file(name: str) -> None:
    prompt = _persona(name)["system_prompt_template"]
    # Every instruction to write a delivery record names Mímir as the target.
    for match in re.finditer(r"(?i)\b(write|update)\b[^\n]*\n?[^\n]*deliveries/\{slug\}", prompt):
        assert "Mímir" in match.group(0), match.group(0)
