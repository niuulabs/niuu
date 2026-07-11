"""Compatibility exports for shared credential mount strategies."""

from credentials.mount_strategies import (
    ApiKeyMountStrategy,
    GenericMountStrategy,
    GitCredentialMountStrategy,
    OAuthTokenMountStrategy,
    SecretMountStrategyRegistry,
    SshKeyMountStrategy,
    TlsCertMountStrategy,
)

__all__ = [
    "ApiKeyMountStrategy",
    "GenericMountStrategy",
    "GitCredentialMountStrategy",
    "OAuthTokenMountStrategy",
    "SecretMountStrategyRegistry",
    "SshKeyMountStrategy",
    "TlsCertMountStrategy",
]
