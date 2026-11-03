"""Agent-maintained session descriptions: what a session is doing, and its fuller status.

The fields are independent of lifecycle state. They are written only through
``SessionRepository.update_description`` so a stale in-memory ``Session`` saved by
the generic update can never overwrite a fresher description.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, field_validator

SHORT_DESCRIPTION_MAX_CHARS = 140
DESCRIPTION_MAX_CHARS = 4000

DESCRIPTION_FIELDS = ("short_description", "description")

_WHITESPACE = re.compile(r"\s+")


def normalize_short_description(value: str | None) -> str | None:
    """One line of plain text: whitespace collapsed, truncated; blank means unset."""
    if value is None:
        return None
    collapsed = _WHITESPACE.sub(" ", value).strip()
    return collapsed[:SHORT_DESCRIPTION_MAX_CHARS].rstrip() or None


def normalize_description(value: str | None) -> str | None:
    """Free-form markdown status, stripped and truncated; blank means unset."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped[:DESCRIPTION_MAX_CHARS].rstrip() or None


class SessionDescriptionPatch(BaseModel):
    """A partial description write.

    A key that is present replaces the stored value (``None`` clears it); an omitted
    key leaves the stored value unchanged. ``model_fields_set`` tells the two apart.
    """

    model_config = ConfigDict(extra="forbid")

    short_description: str | None = None
    description: str | None = None

    @field_validator("short_description")
    @classmethod
    def _normalize_short(cls, value: str | None) -> str | None:
        return normalize_short_description(value)

    @field_validator("description")
    @classmethod
    def _normalize_long(cls, value: str | None) -> str | None:
        return normalize_description(value)

    def changes(self) -> dict[str, str | None]:
        """The normalized values of the keys the caller actually sent."""
        return {
            name: getattr(self, name)
            for name in DESCRIPTION_FIELDS
            if name in self.model_fields_set
        }
