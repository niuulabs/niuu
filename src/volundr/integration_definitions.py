"""Load deployment-owned integration definitions from external files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from volundr.config import IntegrationDefinitionConfig, IntegrationsConfig
from volundr.external_modules import LoadedExternalModule, load_external_module_manifests

logger = logging.getLogger(__name__)


class IntegrationDefinitionLoadError(ValueError):
    """An external integration definition file is missing or invalid."""


def load_integration_definition_configs(
    config: IntegrationsConfig,
    *,
    external_modules: list[LoadedExternalModule] | None = None,
) -> list[IntegrationDefinitionConfig]:
    """Merge built-in/configured definitions with deployment-owned files.

    Each YAML or JSON file may contain a list of definitions, a single
    definition, or a mapping with a ``definitions`` list. Files are evaluated
    in configuration order. Duplicate slugs fail startup unless explicit
    overrides are enabled.
    """
    definitions = list(config.definitions)
    slug_indexes = {definition.slug: index for index, definition in enumerate(definitions)}

    if len(slug_indexes) != len(definitions):
        raise IntegrationDefinitionLoadError(
            "The configured integration catalog contains duplicate slugs"
        )

    definition_files = list(config.definition_files)
    if external_modules is None:
        external_modules = load_external_module_manifests(config.module_manifest_files)
    for external_module in external_modules:
        definition_files.extend(str(path) for path in external_module.integration_definition_files)

    for configured_path in definition_files:
        path = Path(configured_path).expanduser()
        raw_definitions = _read_definition_file(path)
        for item_index, raw_definition in enumerate(raw_definitions):
            definition = _validate_definition(path, item_index, raw_definition)
            existing_index = slug_indexes.get(definition.slug)
            if existing_index is not None:
                if not config.allow_definition_overrides:
                    raise IntegrationDefinitionLoadError(
                        f"Duplicate integration slug '{definition.slug}' in {path}; "
                        "set integrations.allow_definition_overrides=true to replace it"
                    )
                definitions[existing_index] = definition
                logger.info(
                    "External integration definition %s replaced the configured definition",
                    definition.slug,
                )
                continue

            slug_indexes[definition.slug] = len(definitions)
            definitions.append(definition)
            logger.info(
                "Loaded external integration definition %s from %s",
                definition.slug,
                path,
            )

    return definitions


def _read_definition_file(path: Path) -> list[Any]:
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IntegrationDefinitionLoadError(
            f"Could not read integration definition file {path}: {exc}"
        ) from exc

    try:
        document = yaml.safe_load(contents)
    except yaml.YAMLError as exc:
        raise IntegrationDefinitionLoadError(
            f"Invalid YAML/JSON in integration definition file {path}: {exc}"
        ) from exc

    if isinstance(document, list):
        return document
    if isinstance(document, dict) and "definitions" in document:
        definitions = document["definitions"]
        if isinstance(definitions, list):
            return definitions
        raise IntegrationDefinitionLoadError(f"The 'definitions' value in {path} must be a list")
    if isinstance(document, dict):
        return [document]
    raise IntegrationDefinitionLoadError(
        f"Integration definition file {path} must contain a definition, "
        "a list, or a mapping with a 'definitions' list"
    )


def _validate_definition(
    path: Path,
    item_index: int,
    raw_definition: Any,
) -> IntegrationDefinitionConfig:
    try:
        return IntegrationDefinitionConfig.model_validate(raw_definition)
    except ValidationError as exc:
        raise IntegrationDefinitionLoadError(
            f"Invalid integration definition at {path} item {item_index + 1}: {exc}"
        ) from exc
