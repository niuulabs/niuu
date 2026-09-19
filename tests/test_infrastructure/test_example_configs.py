"""The files in examples/ must describe the configuration the code actually reads.

Every settings model here uses ``extra="ignore"``, so a key the code no longer
defines is accepted silently — which is how the examples came to document the
removed ``profiles``/``templates`` and ReviewEngine settings for months. These
tests fail on any key the target model does not define, and on any value it
rejects.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

from bifrost.config import BifrostConfig
from ravn.config import Settings as RavnSettings
from ravn.tui.keybindings.loader import KeybindingConfig
from ting.config import Settings as TingSettings
from volundr.config import Settings as VolundrSettings

EXAMPLES = Path(__file__).parents[2] / "examples"


def _nested_model(annotation: Any) -> tuple[str, type[BaseModel] | None]:
    """Classify a field annotation as a model, a list/dict of models, or free-form."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        for arg in typing.get_args(annotation):
            kind, model = _nested_model(arg)
            if kind != "free":
                return kind, model
        return "free", None
    if origin in (list, tuple, set):
        args = typing.get_args(annotation)
        kind, model = _nested_model(args[0]) if args else ("free", None)
        return ("list", model) if kind == "model" else ("free", None)
    if origin is dict:
        args = typing.get_args(annotation)
        kind, model = _nested_model(args[1]) if len(args) == 2 else ("free", None)
        return ("dict", model) if kind == "model" else ("free", None)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return "model", annotation
    return "free", None


def _accepted_names(model: type[BaseModel]) -> dict[str, Any]:
    names: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        names[name] = field
        if field.alias:
            names[field.alias] = field
        alias = field.validation_alias
        choices = getattr(alias, "choices", [alias] if isinstance(alias, str) else [])
        for choice in choices:
            if isinstance(choice, str):
                names[choice] = field
    return names


def _unknown_keys(data: Any, model: type[BaseModel], path: str = "") -> list[str]:
    if not isinstance(data, dict):
        return []
    accepted = _accepted_names(model)
    unknown: list[str] = []
    for key, value in data.items():
        if key not in accepted:
            unknown.append(f"{path}{key}")
            continue
        kind, nested = _nested_model(accepted[key].annotation)
        if kind == "model":
            unknown += _unknown_keys(value, nested, f"{path}{key}.")
        if kind == "list" and isinstance(value, list):
            for index, item in enumerate(value):
                unknown += _unknown_keys(item, nested, f"{path}{key}[{index}].")
        if kind == "dict" and isinstance(value, dict):
            for name, item in value.items():
                unknown += _unknown_keys(item, nested, f"{path}{key}.{name}.")
    return unknown


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((EXAMPLES / name).read_text())


@pytest.mark.parametrize(
    ("filename", "model", "wrapper"),
    [
        ("volundr.yaml", VolundrSettings, None),
        ("ting.yaml", TingSettings, None),
        ("ravn.yaml", RavnSettings, None),
        # bifrost.__main__ unwraps the top-level ``bifrost:`` key before validating.
        ("bifrost.yaml", BifrostConfig, "bifrost"),
        ("bifrost-pi.yaml", BifrostConfig, "bifrost"),
    ],
)
def test_example_matches_its_settings_model(
    filename: str, model: type[BaseModel], wrapper: str | None
) -> None:
    data = _load(filename)
    if wrapper is not None:
        assert list(data) == [wrapper], f"{filename} must have one top-level key: {wrapper}"
        data = data[wrapper]

    assert _unknown_keys(data, model) == [], (
        f"examples/{filename} names keys that {model.__module__}.{model.__name__} does not "
        "define; the code ignores them silently. Update the example."
    )
    model.model_validate(data)


def test_ravn_tui_example_matches_the_keybinding_loader() -> None:
    data = _load("ravn-tui.yaml")
    assert list(data) == ["keybindings"]

    known = {field.name for field in dataclasses.fields(KeybindingConfig)}
    assert set(data["keybindings"]) <= known


def test_every_example_is_covered_here() -> None:
    covered = {
        "volundr.yaml",
        "ting.yaml",
        "ravn.yaml",
        "ravn-tui.yaml",
        "bifrost.yaml",
        "bifrost-pi.yaml",
        # Not a settings file: a template for a Claude Code .mcp.json.
        "mcp.json",
        "README.md",
    }
    present = {path.name for path in EXAMPLES.iterdir() if path.is_file()}
    assert present == covered, "add a check above for any new example file"
