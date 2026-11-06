"""Errors the guest agent raises. Every one carries the remedy or the cause in its message."""

from __future__ import annotations


class GuestAgentError(Exception):
    """Base class: the agent cannot do what its configuration asks."""


class ConfigError(GuestAgentError):
    """The agent's config file is missing, malformed, or has a bad field."""


class SignerError(GuestAgentError):
    """The agent could not produce the proof of identity a request needs."""


class FetchError(GuestAgentError):
    """Forge's metadata endpoint did not return usable credentials."""


class WriteError(GuestAgentError):
    """A credential file could not be written."""
