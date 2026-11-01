"""Review widgets a deployment installs from its external module packages.

A widget is one HTML file a package ships. It runs in the same sandbox as any
page a workflow brings, so installing one grants nothing beyond what a page has.
Reading a manifest here stays free of the Python components it also declares.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

WIDGET_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"


class ReviewWidgetError(ValueError):
    """A declared review widget cannot be used."""


class ReviewWidgetDeclaration(BaseModel):
    """One review widget in a module manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, pattern=WIDGET_NAME_PATTERN)
    title: str = Field(min_length=1)
    entry: str = Field(min_length=1)

    @field_validator("entry")
    @classmethod
    def _entry_stays_in_the_package(cls, value: str) -> str:
        path = PurePosixPath(value.strip())
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("entry must be a path within the package")
        return str(path)


class _ManifestWidgets(BaseModel):
    """Only what a reader of widgets needs from a manifest."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, pattern=WIDGET_NAME_PATTERN)
    review_widgets: tuple[ReviewWidgetDeclaration, ...] = ()


@dataclass(frozen=True)
class ReviewWidget:
    module: str
    name: str
    title: str
    html: str

    @property
    def widget_id(self) -> str:
        return f"{self.module}/{self.name}"


def _read_entry(root: Path, module: str, declared: ReviewWidgetDeclaration, max_bytes: int) -> str:
    path = (root / declared.entry).resolve()
    if not path.is_relative_to(root):
        raise ReviewWidgetError(
            f"Review widget {module}/{declared.name} resolves outside its package: {declared.entry}"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReviewWidgetError(
            f"Could not read review widget {module}/{declared.name} from {path}: {exc}"
        ) from exc
    if len(raw) > max_bytes:
        raise ReviewWidgetError(
            f"Review widget {module}/{declared.name} is {len(raw)} bytes, over the {max_bytes} "
            "byte limit. Raise review_widgets.max_page_bytes, or have the page ask for large "
            "assets as declared files."
        )
    return raw.decode("utf-8")


def _read_manifest(manifest: Path, max_bytes: int) -> tuple[ReviewWidget, ...]:
    try:
        document = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        parsed = _ManifestWidgets.model_validate(document)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ReviewWidgetError(f"Invalid module manifest {manifest}: {exc}") from exc
    root = manifest.resolve().parent
    return tuple(
        ReviewWidget(
            module=parsed.id,
            name=declared.name,
            title=declared.title,
            html=_read_entry(root, parsed.id, declared, max_bytes),
        )
        for declared in parsed.review_widgets
    )


def load_review_widgets(manifests: list[str], *, max_bytes: int) -> dict[str, ReviewWidget]:
    """Load every declared widget, or fail before anything serves a broken one."""
    widgets: dict[str, ReviewWidget] = {}
    for configured in manifests:
        for widget in _read_manifest(Path(configured).expanduser(), max_bytes):
            if widget.widget_id in widgets:
                raise ReviewWidgetError(f"Duplicate review widget {widget.widget_id!r}")
            widgets[widget.widget_id] = widget
    return widgets
