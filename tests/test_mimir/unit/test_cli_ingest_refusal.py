"""``python -m mimir ingest`` refuses the source types REST /ingest refuses."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mimir.__main__ import _run_mimir_ingest


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["tool_output", "diagnostic"])
async def test_cli_ingest_refuses_operational_sources(tmp_path, source_type: str) -> None:
    doc = tmp_path / "run.log"
    doc.write_text("probe output", encoding="utf-8")
    with (
        patch("mimir.builders.build_mimir") as build,
        pytest.raises(ValueError, match=f"Refusing to ingest source_type {source_type!r}"),
    ):
        await _run_mimir_ingest(SimpleNamespace(), str(doc), "Run log", source_type, None)
    build.assert_not_called()
