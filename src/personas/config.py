"""Configuration models for persona sources."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PersonaSourceConfig(BaseModel):
    """Config for a persona configuration source.

    By default personas come from the built-in catalogue and YAML files on disk.
    Point ``persona_source.adapter`` at any class implementing
    :class:`~personas.ports.persona.PersonaPort` to use a different source
    (database, remote API, generated at runtime, etc.).

    Constructor kwargs are forwarded directly to the adapter class, so the
    ``kwargs`` dict must match the adapter's ``__init__`` signature.

    Example::

        persona_source:
          adapter: personas.adapters.loader.FilesystemPersonaAdapter
          kwargs:
            persona_dirs: [".ravn/personas", "~/.ravn/personas"]
    """

    adapter: str = Field(
        default="personas.adapters.loader.FilesystemPersonaAdapter",
        description="Fully-qualified class path for the PersonaPort implementation.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Keyword arguments passed to the adapter constructor.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Map of kwarg name → env var name for secret values.",
    )
