"""Shared response models for mounted settings schema endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SettingsOptionSchema(BaseModel):
    """One selectable option for a mounted settings field."""

    label: str
    value: str


class SettingsFieldSchema(BaseModel):
    """One form field in a mounted settings section."""

    model_config = ConfigDict(populate_by_name=True)

    key: str
    label: str
    type: Literal["text", "textarea", "number", "boolean", "select"]
    value: str | int | float | bool | None
    description: str | None = None
    placeholder: str | None = None
    read_only: bool = Field(default=False, serialization_alias="readOnly")
    secret: bool = False
    options: list[SettingsOptionSchema] | None = None


class SettingsSectionSchema(BaseModel):
    """One mounted settings section."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    label: str
    description: str | None = None
    path: str | None = None
    save_label: str | None = Field(default=None, serialization_alias="saveLabel")
    fields: list[SettingsFieldSchema]


class SettingsProviderSchema(BaseModel):
    """Mounted settings provider payload returned by ``GET /settings``."""

    model_config = ConfigDict(populate_by_name=True)

    title: str
    subtitle: str | None = None
    scope: Literal["user", "service", "admin"] = "service"
    sections: list[SettingsSectionSchema]
