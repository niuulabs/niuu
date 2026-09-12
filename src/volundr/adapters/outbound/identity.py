"""Compatibility imports for existing identity adapter configuration paths."""

from identity.adapters.identity import AllowAllIdentityAdapter, EnvoyHeaderIdentityAdapter

__all__ = ["AllowAllIdentityAdapter", "EnvoyHeaderIdentityAdapter"]
