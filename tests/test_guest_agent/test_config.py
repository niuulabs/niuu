from __future__ import annotations

import json
from pathlib import Path

import pytest

from volundr.guest_agent.config import (
    BearerFileAuth,
    ForgeSessionTarget,
    HostKeyAuth,
    SpiffeAuth,
    load_config,
    parse_config,
)
from volundr.guest_agent.errors import ConfigError

SPIFFE_AUTH = {
    "kind": "spiffe",
    "audience": "forge",
    "socket_path": "/run/spire/agent/api.sock",
    "spire_agent_binary": "/opt/spire/bin/spire-agent",
}


def test_valid_host_key_config(config_dict: dict) -> None:
    config = parse_config(config_dict)

    assert isinstance(config.auth, HostKeyAuth)
    assert config.allocation_id == "8d3f2a52-0c47-4a49-9b52-3f7a1f1c2d10"
    assert config.token_volumes[0].volume == "identity"
    assert config.token_volumes[0].tokens[0].path == "token"
    assert isinstance(config.forge_session, ForgeSessionTarget)
    assert config.refresh_fraction == 0.8
    assert config.ca_file == ""


def test_valid_spiffe_config(make_config) -> None:
    config = parse_config(make_config(auth=SPIFFE_AUTH))

    assert config.auth == SpiffeAuth(
        audience="forge",
        socket_path="/run/spire/agent/api.sock",
        spire_agent_binary="/opt/spire/bin/spire-agent",
    )


def test_valid_bearer_file_config(make_config) -> None:
    config = parse_config(
        make_config(auth={"kind": "bearer_file", "token_path": "/etc/niuu/guest-identity/token"})
    )

    assert config.auth == BearerFileAuth(token_path="/etc/niuu/guest-identity/token")


@pytest.mark.parametrize(
    ("auth", "message"),
    [
        ({"kind": "bearer_file"}, "auth.token_path: missing field"),
        ({"kind": "bearer_file", "token_path": "token"}, "must be an absolute path"),
        ({"kind": "bearer_file", "token_path": "/t", "audience": "x"}, "auth.audience: unknown"),
    ],
    ids=["missing", "relative", "unknown-field"],
)
def test_bearer_file_config_is_strict(make_config, auth: dict, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(make_config(auth=auth))


def test_forge_session_may_be_null(make_config) -> None:
    assert parse_config(make_config(forge_session=None)).forge_session is None


def test_load_config_reads_the_file(config_file: Path) -> None:
    assert load_config(config_file).metadata_url == "https://forge.test:8443/"


def test_load_config_names_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read the config file"):
        load_config(tmp_path / "absent.json")


def test_load_config_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json")

    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(path)


@pytest.mark.parametrize(
    "field",
    [
        "metadata_url",
        "allocation_id",
        "auth",
        "token_volumes",
        "forge_session",
        "listen",
        "refresh_fraction",
        "retry_seconds",
        "request_timeout_seconds",
        "ca_file",
    ],
)
def test_missing_top_level_field_is_named(config_dict: dict, field: str) -> None:
    del config_dict[field]

    with pytest.raises(ConfigError, match=f"^{field}: missing field"):
        parse_config(config_dict)


def test_unknown_top_level_field_is_named(config_dict: dict) -> None:
    config_dict["refresh_every"] = 5

    with pytest.raises(ConfigError, match="^refresh_every: unknown field"):
        parse_config(config_dict)


@pytest.mark.parametrize(
    ("path", "mutate", "message"),
    [
        ("auth.extra", lambda d: d["auth"].update(extra=1), "auth.extra: unknown field"),
        ("auth.key_path", lambda d: d["auth"].pop("key_path"), "auth.key_path: missing field"),
        ("auth.kind", lambda d: d["auth"].pop("kind"), "auth.kind: missing field"),
        ("auth.kind", lambda d: d["auth"].update(kind="oidc"), "auth.kind: unknown kind"),
        (
            "auth.key_path",
            lambda d: d["auth"].update(key_path="relative"),
            "auth.key_path: must be an absolute path",
        ),
        (
            "token_volumes[0].directory",
            lambda d: d["token_volumes"][0].pop("directory"),
            r"token_volumes\[0\]\.directory: missing field",
        ),
        (
            "token_volumes[0].tokens[0].extra",
            lambda d: d["token_volumes"][0]["tokens"][0].update(extra=1),
            r"token_volumes\[0\]\.tokens\[0\]\.extra: unknown field",
        ),
        (
            "token_volumes[0].tokens[0].path",
            lambda d: d["token_volumes"][0]["tokens"][0].update(path="../escape"),
            r"token_volumes\[0\]\.tokens\[0\]\.path: must be a relative path",
        ),
        (
            "token_volumes[0].tokens[0].path",
            lambda d: d["token_volumes"][0]["tokens"][0].update(path="/abs"),
            r"token_volumes\[0\]\.tokens\[0\]\.path: must be a relative path",
        ),
        (
            "token_volumes[0].tokens",
            lambda d: d["token_volumes"][0].update(tokens="token"),
            r"token_volumes\[0\]\.tokens: expected a list",
        ),
        (
            "forge_session.file",
            lambda d: d["forge_session"].update(file=""),
            "forge_session.file: must not be empty",
        ),
        (
            "forge_session.directory",
            lambda d: d["forge_session"].pop("directory"),
            "forge_session.directory: missing field",
        ),
        (
            "listen.port",
            lambda d: d["listen"].update(port="80"),
            "listen.port: expected an integer",
        ),
        ("listen.port", lambda d: d["listen"].update(port=70000), "listen.port: must be between"),
        (
            "listen.port",
            lambda d: d["listen"].update(port=True),
            "listen.port: expected an integer",
        ),
        (
            "listen.address",
            lambda d: d["listen"].update(address="metadata.local"),
            "listen.address: not an IP address",
        ),
        (
            "allocation_id",
            lambda d: d.update(allocation_id="not-a-uuid"),
            "allocation_id: not a UUID",
        ),
        (
            "metadata_url",
            lambda d: d.update(metadata_url="http://forge.test"),
            "metadata_url: must be an https URL",
        ),
        ("metadata_url", lambda d: d.update(metadata_url=3), "metadata_url: expected a string"),
        (
            "refresh_fraction",
            lambda d: d.update(refresh_fraction=1.0),
            "refresh_fraction: must be between 0 and 1",
        ),
        (
            "refresh_fraction",
            lambda d: d.update(refresh_fraction=0),
            "refresh_fraction: must be greater than 0",
        ),
        (
            "retry_seconds",
            lambda d: d.update(retry_seconds=-1),
            "retry_seconds: must be greater than 0",
        ),
        (
            "request_timeout_seconds",
            lambda d: d.update(request_timeout_seconds="30"),
            "request_timeout_seconds: expected a number",
        ),
        ("ca_file", lambda d: d.update(ca_file=None), "ca_file: expected a string"),
    ],
)
def test_bad_field_is_named(config_dict: dict, path: str, mutate, message: str) -> None:
    mutate(config_dict)

    with pytest.raises(ConfigError, match=message):
        parse_config(config_dict)


def test_spiffe_requires_every_field(make_config) -> None:
    auth = dict(SPIFFE_AUTH)
    del auth["audience"]

    with pytest.raises(ConfigError, match="auth.audience: missing field"):
        parse_config(make_config(auth=auth))


def test_duplicate_token_is_rejected(config_dict: dict) -> None:
    config_dict["token_volumes"][0]["tokens"].append({"path": "token"})

    with pytest.raises(ConfigError, match="duplicate token identity/token"):
        parse_config(config_dict)


def test_nothing_to_deliver_is_rejected(make_config) -> None:
    with pytest.raises(ConfigError, match="nothing to deliver"):
        parse_config(make_config(token_volumes=[], forge_session=None))


def test_top_level_must_be_an_object() -> None:
    with pytest.raises(ConfigError, match="expected an object"):
        parse_config(json.loads("[]"))
