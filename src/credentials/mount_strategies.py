"""Secret mount strategies for each SecretType.

Each strategy knows how to validate secret data and generate
a default SecretMountSpec for its type.
"""

from __future__ import annotations

from credentials.models import MountType, SecretMountSpec, SecretType
from credentials.ports import SecretMountStrategy


class ApiKeyMountStrategy(SecretMountStrategy):
    """Mounts API keys as environment variables."""

    def secret_type(self) -> SecretType:
        return SecretType.API_KEY

    def default_mount_spec(
        self,
        secret_path: str,
        secret_data: dict,
    ) -> SecretMountSpec:
        return SecretMountSpec(
            secret_path=secret_path,
            mount_type=MountType.ENV_FILE,
            destination="/run/secrets/api-keys.env",
        )

    def validate(self, secret_data: dict) -> list[str]:
        if not secret_data:
            return ["At least one key-value pair is required"]
        return []


class OAuthTokenMountStrategy(SecretMountStrategy):
    """Mounts OAuth tokens as environment variables."""

    def secret_type(self) -> SecretType:
        return SecretType.OAUTH_TOKEN

    def default_mount_spec(
        self,
        secret_path: str,
        secret_data: dict,
    ) -> SecretMountSpec:
        return SecretMountSpec(
            secret_path=secret_path,
            mount_type=MountType.ENV_FILE,
            destination="/run/secrets/oauth-token.env",
        )

    def validate(self, secret_data: dict) -> list[str]:
        errors: list[str] = []
        if "access_token" not in secret_data:
            errors.append("'access_token' field is required")
        return errors


class GitCredentialMountStrategy(SecretMountStrategy):
    """Mounts git credentials as a credentials file."""

    def secret_type(self) -> SecretType:
        return SecretType.GIT_CREDENTIAL

    def default_mount_spec(
        self,
        secret_path: str,
        secret_data: dict,
    ) -> SecretMountSpec:
        return SecretMountSpec(
            secret_path=secret_path,
            mount_type=MountType.FILE,
            destination="/home/volundr/.git-credentials",
        )

    def validate(self, secret_data: dict) -> list[str]:
        errors: list[str] = []
        if "url" not in secret_data:
            errors.append("'url' field is required (e.g. https://user:token@github.com)")
        return errors


class SshKeyMountStrategy(SecretMountStrategy):
    """Mounts SSH keys as files."""

    def secret_type(self) -> SecretType:
        return SecretType.SSH_KEY

    def default_mount_spec(
        self,
        secret_path: str,
        secret_data: dict,
    ) -> SecretMountSpec:
        return SecretMountSpec(
            secret_path=secret_path,
            mount_type=MountType.FILE,
            destination="/home/volundr/.ssh/id_rsa",
        )

    def validate(self, secret_data: dict) -> list[str]:
        errors: list[str] = []
        if "private_key" not in secret_data:
            errors.append("'private_key' field is required")
        return errors


class TlsCertMountStrategy(SecretMountStrategy):
    """Mounts TLS certificate + key as files."""

    def secret_type(self) -> SecretType:
        return SecretType.TLS_CERT

    def default_mount_spec(
        self,
        secret_path: str,
        secret_data: dict,
    ) -> SecretMountSpec:
        return SecretMountSpec(
            secret_path=secret_path,
            mount_type=MountType.TEMPLATE,
            destination="/run/secrets/tls/",
            template=(
                '{{ with secret "' + secret_path + '" }}{{ .Data.data.certificate }}{{ end }}'
            ),
        )

    def validate(self, secret_data: dict) -> list[str]:
        errors: list[str] = []
        if "certificate" not in secret_data:
            errors.append("'certificate' field is required")
        if "private_key" not in secret_data:
            errors.append("'private_key' field is required")
        return errors


class GenericMountStrategy(SecretMountStrategy):
    """Mounts generic secrets as env files."""

    def secret_type(self) -> SecretType:
        return SecretType.GENERIC

    def default_mount_spec(
        self,
        secret_path: str,
        secret_data: dict,
    ) -> SecretMountSpec:
        return SecretMountSpec(
            secret_path=secret_path,
            mount_type=MountType.ENV_FILE,
            destination="/run/secrets/generic.env",
        )

    def validate(self, secret_data: dict) -> list[str]:
        if not secret_data:
            return ["At least one key-value pair is required"]
        return []


class SecretMountStrategyRegistry:
    """Registry mapping SecretType to its mount strategy."""

    def __init__(self) -> None:
        self._strategies: dict[SecretType, SecretMountStrategy] = {}
        # Register all built-in strategies
        for strategy_cls in (
            ApiKeyMountStrategy,
            OAuthTokenMountStrategy,
            GitCredentialMountStrategy,
            SshKeyMountStrategy,
            TlsCertMountStrategy,
            GenericMountStrategy,
        ):
            strategy = strategy_cls()
            self._strategies[strategy.secret_type()] = strategy

    def get(self, secret_type: SecretType) -> SecretMountStrategy:
        """Get the strategy for a secret type.

        Falls back to GenericMountStrategy if no specific strategy exists.
        """
        return self._strategies.get(
            secret_type,
            self._strategies[SecretType.GENERIC],
        )

    def list_types(self) -> list[dict]:
        """Return type info for all registered strategies."""
        type_info = {
            SecretType.API_KEY: {
                "type": "api_key",
                "label": "API Key",
                "description": "API keys for external services",
                "fields": [
                    {"key": "api_key", "label": "API Key", "type": "password", "required": True},
                ],
                "default_mount_type": "env",
            },
            SecretType.OAUTH_TOKEN: {
                "type": "oauth_token",
                "label": "OAuth Token",
                "description": "OAuth access and refresh tokens",
                "fields": [
                    {
                        "key": "access_token",
                        "label": "Access Token",
                        "type": "password",
                        "required": True,
                    },
                    {
                        "key": "refresh_token",
                        "label": "Refresh Token",
                        "type": "password",
                        "required": False,
                    },
                ],
                "default_mount_type": "env",
            },
            SecretType.GIT_CREDENTIAL: {
                "type": "git_credential",
                "label": "Git Credential",
                "description": "Git authentication credentials",
                "fields": [
                    {"key": "url", "label": "Credential URL", "type": "text", "required": True},
                ],
                "default_mount_type": "file",
            },
            SecretType.SSH_KEY: {
                "type": "ssh_key",
                "label": "SSH Key",
                "description": "SSH private key for authentication",
                "fields": [
                    {
                        "key": "private_key",
                        "label": "Private Key",
                        "type": "textarea",
                        "required": True,
                    },
                    {
                        "key": "public_key",
                        "label": "Public Key",
                        "type": "textarea",
                        "required": False,
                    },
                ],
                "default_mount_type": "file",
            },
            SecretType.TLS_CERT: {
                "type": "tls_cert",
                "label": "TLS Certificate",
                "description": "TLS certificate and private key pair",
                "fields": [
                    {
                        "key": "certificate",
                        "label": "Certificate",
                        "type": "textarea",
                        "required": True,
                    },
                    {
                        "key": "private_key",
                        "label": "Private Key",
                        "type": "textarea",
                        "required": True,
                    },
                ],
                "default_mount_type": "file",
            },
            SecretType.GENERIC: {
                "type": "generic",
                "label": "Generic Secret",
                "description": "Custom key-value secret data",
                "fields": [],
                "default_mount_type": "env",
            },
        }
        return [type_info[st] for st in SecretType if st in type_info]
