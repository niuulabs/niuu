"""Shared response models for mounted settings schema endpoints."""

from __future__ import annotations

from typing import Annotated, Literal

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


class SettingsResourceSchemaBase(BaseModel):
    """Shared metadata for richer mounted settings resources."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    label: str
    description: str | None = None
    writable: bool = True


class SettingsTokensResourceSchema(SettingsResourceSchemaBase):
    """Resource descriptor for PAT management inside the mounted shell."""

    type: Literal["tokens"] = "tokens"
    list_path: str = Field(serialization_alias="listPath")
    create_path: str = Field(serialization_alias="createPath")
    delete_path: str = Field(serialization_alias="deletePath")


class SettingsCredentialsResourceSchema(SettingsResourceSchemaBase):
    """Resource descriptor for stored credential management."""

    type: Literal["credentials"] = "credentials"
    list_path: str = Field(serialization_alias="listPath")
    types_path: str = Field(serialization_alias="typesPath")
    create_path: str = Field(serialization_alias="createPath")
    delete_path: str = Field(serialization_alias="deletePath")


class SettingsIntegrationsResourceSchema(SettingsResourceSchemaBase):
    """Resource descriptor for integration management inside the mounted shell."""

    type: Literal["integrations"] = "integrations"
    list_path: str = Field(serialization_alias="listPath")
    catalog_path: str = Field(serialization_alias="catalogPath")
    create_path: str = Field(serialization_alias="createPath")
    delete_path: str = Field(serialization_alias="deletePath")
    credential_list_path: str = Field(serialization_alias="credentialListPath")
    test_path: str = Field(serialization_alias="testPath")
    oauth_authorize_path: str = Field(serialization_alias="oauthAuthorizePath")
    oauth_disconnect_path: str = Field(serialization_alias="oauthDisconnectPath")
    enrollment_start_path: str = Field(serialization_alias="enrollmentStartPath")
    enrollment_status_path: str = Field(serialization_alias="enrollmentStatusPath")
    enrollment_cancel_path: str = Field(serialization_alias="enrollmentCancelPath")


SettingsResourceSchema = Annotated[
    SettingsTokensResourceSchema
    | SettingsCredentialsResourceSchema
    | SettingsIntegrationsResourceSchema,
    Field(discriminator="type"),
]


class SettingsSectionSchema(BaseModel):
    """One mounted settings section."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    label: str
    description: str | None = None
    path: str | None = None
    save_label: str | None = Field(default=None, serialization_alias="saveLabel")
    fields: list[SettingsFieldSchema]
    resources: list[SettingsResourceSchema] = Field(default_factory=list)


class SettingsProviderSchema(BaseModel):
    """Mounted settings provider payload returned by ``GET /settings``."""

    model_config = ConfigDict(populate_by_name=True)

    title: str
    subtitle: str | None = None
    scope: Literal["user", "service", "admin"] = "service"
    sections: list[SettingsSectionSchema]
