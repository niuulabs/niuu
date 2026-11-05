"""Every bundled persona's voice.tools builds on a resident.

The voice channel refuses to start when a persona names a voice tool it cannot
build, which stops the whole resident. This builds each bundled persona's tools
with the real builder under a resident's platform settings, so a persona that
names an unbuildable voice tool fails here instead of at boot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ravn.adapters.personas.loader import _BUILTIN_PERSONAS_DIR, FilesystemPersonaAdapter
from ravn.cli.commands import _build_tool_mcp_tools
from ravn.config import Settings


def _voice_personas() -> list[str]:
    loader = FilesystemPersonaAdapter(
        persona_dirs=[str(_BUILTIN_PERSONAS_DIR)], include_builtin=False
    )
    return [
        name
        for name in loader.list_names()
        if (persona := loader.load(name)) is not None and persona.voice.enabled
    ]


def _resident_settings() -> Settings:
    """What a resident with resident.platform.enabled runs with (charts/skuld)."""
    settings = Settings()
    settings.gateway.platform.enabled = True
    settings.gateway.platform.base_url = "https://niuu.example.test"
    return settings


def test_some_bundled_persona_has_a_voice() -> None:
    assert "sidekick" in _voice_personas()


@pytest.mark.parametrize("name", _voice_personas())
def test_voice_tools_build_on_a_resident(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    loader = FilesystemPersonaAdapter(
        persona_dirs=[str(_BUILTIN_PERSONAS_DIR)], include_builtin=False, cwd=tmp_path
    )
    persona = loader.load(name)
    assert persona is not None

    built = {
        tool.name for tool in _build_tool_mcp_tools(_resident_settings(), persona_config=persona)
    }

    assert [tool for tool in persona.voice.tools if tool not in built] == []
