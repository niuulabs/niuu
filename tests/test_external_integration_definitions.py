"""Tests for deployment-owned external integration definitions."""

from __future__ import annotations

import json

import pytest

from volundr.config import IntegrationDefinitionConfig, IntegrationsConfig
from volundr.integration_definitions import (
    IntegrationDefinitionLoadError,
    load_integration_definition_configs,
)


def _definition(slug: str, name: str = "External tracker") -> dict[str, str]:
    return {
        "slug": slug,
        "name": name,
        "integration_type": "issue_tracker",
        "adapter": "external_tracker.Adapter",
    }


def test_loads_yaml_list_and_json_envelope(tmp_path) -> None:
    yaml_file = tmp_path / "first.yaml"
    yaml_file.write_text(
        "- slug: external-one\n"
        "  name: External one\n"
        "  integration_type: issue_tracker\n"
        "  adapter: external_one.Adapter\n",
        encoding="utf-8",
    )
    json_file = tmp_path / "second.json"
    json_file.write_text(
        json.dumps({"definitions": [_definition("external-two")]}),
        encoding="utf-8",
    )

    loaded = load_integration_definition_configs(
        IntegrationsConfig(definitions=[], definition_files=[str(yaml_file), str(json_file)])
    )

    assert [definition.slug for definition in loaded] == ["external-one", "external-two"]


def test_preserves_configured_definitions_before_external_definitions(tmp_path) -> None:
    definition_file = tmp_path / "external.yaml"
    definition_file.write_text(
        "slug: external\n"
        "name: External\n"
        "integration_type: issue_tracker\n"
        "adapter: external.Adapter\n",
        encoding="utf-8",
    )

    loaded = load_integration_definition_configs(
        IntegrationsConfig(
            definitions=[IntegrationDefinitionConfig(**_definition("configured"))],
            definition_files=[str(definition_file)],
        )
    )

    assert [definition.slug for definition in loaded] == ["configured", "external"]


def test_duplicate_slug_fails_by_default(tmp_path) -> None:
    definition_file = tmp_path / "duplicate.yaml"
    definition_file.write_text(
        "slug: configured\n"
        "name: Replacement\n"
        "integration_type: issue_tracker\n"
        "adapter: replacement.Adapter\n",
        encoding="utf-8",
    )

    with pytest.raises(IntegrationDefinitionLoadError, match="Duplicate integration slug"):
        load_integration_definition_configs(
            IntegrationsConfig(
                definitions=[IntegrationDefinitionConfig(**_definition("configured"))],
                definition_files=[str(definition_file)],
            )
        )


def test_explicit_override_replaces_definition_in_place(tmp_path) -> None:
    definition_file = tmp_path / "override.yaml"
    definition_file.write_text(
        "slug: configured\n"
        "name: Replacement\n"
        "integration_type: issue_tracker\n"
        "adapter: replacement.Adapter\n",
        encoding="utf-8",
    )

    loaded = load_integration_definition_configs(
        IntegrationsConfig(
            definitions=[IntegrationDefinitionConfig(**_definition("configured"))],
            definition_files=[str(definition_file)],
            allow_definition_overrides=True,
        )
    )

    assert len(loaded) == 1
    assert loaded[0].name == "Replacement"
    assert loaded[0].adapter == "replacement.Adapter"


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("definitions: invalid", "must be a list"),
        ("- name: Missing slug", "Invalid integration definition"),
        ("", "must contain a definition"),
    ],
)
def test_invalid_external_file_fails_startup(tmp_path, contents: str, message: str) -> None:
    definition_file = tmp_path / "invalid.yaml"
    definition_file.write_text(contents, encoding="utf-8")

    with pytest.raises(IntegrationDefinitionLoadError, match=message):
        load_integration_definition_configs(
            IntegrationsConfig(definitions=[], definition_files=[str(definition_file)])
        )
