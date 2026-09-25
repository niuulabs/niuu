"""Tests for cli.config.persist_guild_join / _merge_identity_trust.

Blocker this file guards: `niuu join` must never lower or replace this
host's own auth mode, must never write an unparseable `host_auth.mode`
(e.g. Guild's raw 'envoy'), and must always validate the result through
AuthConfig before writing.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from cli.config import AuthConfig, AuthOidcConfig, OidcIssuerConfig, persist_guild_join


def _oidc_config(*issuers: OidcIssuerConfig) -> AuthConfig:
    return AuthConfig(mode="oidc", oidc=AuthOidcConfig(issuers=list(issuers)))


def _issuer(url: str, audiences: list[str] | None = None) -> OidcIssuerConfig:
    return OidcIssuerConfig(issuer=url, audiences=audiences or ["volundr-api"])


def test_none_host_adopts_an_oidc_guild(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"

    warning = persist_guild_join(
        url="https://guild.example.com",
        node_id="node-1",
        current_host_auth=AuthConfig(),  # default: mode=none
        identity_trust={
            "mode": "oidc",
            "issuers": [{"issuer": "https://idp.example.com", "audiences": ["x"]}],
        },
        config_file=config_file,
    )

    assert warning is None
    saved = yaml.safe_load(config_file.read_text())
    assert saved["host_auth"]["mode"] == "oidc"
    AuthConfig(**saved["host_auth"])  # round-trips


def test_envoy_mode_is_treated_as_oidc_not_written_literally(tmp_path: Path) -> None:
    """The actual blocker: a K8s Guild reports 'envoy', which AuthConfig.mode
    (Literal['none', 'oidc']) cannot parse -- it must never be written."""
    config_file = tmp_path / "config.yaml"

    warning = persist_guild_join(
        url="https://guild.example.com",
        node_id="node-1",
        current_host_auth=AuthConfig(),
        identity_trust={
            "mode": "envoy",
            "issuers": [{"issuer": "https://idp.example.com", "audiences": ["x"]}],
        },
        config_file=config_file,
    )

    assert warning is None
    saved = yaml.safe_load(config_file.read_text())
    assert saved["host_auth"]["mode"] == "oidc"
    # Round-trips through the SAME AuthConfig every later CLI invocation
    # loads config.yaml through -- this is the failure mode the blocker
    # describes ("every later CLI config load fails").
    parsed = AuthConfig(**saved["host_auth"])
    assert parsed.mode == "oidc"


def test_never_downgrades_an_existing_oidc_host_to_none(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    current = _oidc_config(_issuer("https://existing-idp.example.com"))

    warning = persist_guild_join(
        url="https://guild.example.com",
        node_id="node-1",
        current_host_auth=current,
        identity_trust={"mode": "none", "issuers": []},
        config_file=config_file,
    )

    assert warning is not None
    assert "downgrade" in warning.lower()
    saved = yaml.safe_load(config_file.read_text())
    # Unchanged: still oidc, still trusting the pre-existing issuer.
    assert saved["host_auth"]["mode"] == "oidc"
    assert saved["host_auth"]["oidc"]["issuers"][0]["issuer"] == "https://existing-idp.example.com"


def test_none_host_stays_none_when_guild_is_none(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"

    warning = persist_guild_join(
        url="https://guild.example.com",
        node_id="node-1",
        current_host_auth=AuthConfig(),
        identity_trust={"mode": "none", "issuers": []},
        config_file=config_file,
    )

    assert warning is None
    saved = yaml.safe_load(config_file.read_text())
    assert saved["host_auth"]["mode"] == "none"


def test_merges_issuers_rather_than_replacing_the_existing_list(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    current = _oidc_config(_issuer("https://existing-idp.example.com"))

    warning = persist_guild_join(
        url="https://guild.example.com",
        node_id="node-1",
        current_host_auth=current,
        identity_trust={
            "mode": "oidc",
            "issuers": [{"issuer": "https://guild-idp.example.com", "audiences": ["y"]}],
        },
        config_file=config_file,
    )

    assert warning is None
    saved = yaml.safe_load(config_file.read_text())
    issuer_urls = {entry["issuer"] for entry in saved["host_auth"]["oidc"]["issuers"]}
    assert issuer_urls == {"https://existing-idp.example.com", "https://guild-idp.example.com"}


def test_reports_and_updates_the_same_issuer_by_url(tmp_path: Path) -> None:
    """A Guild re-reporting an issuer this host already trusts (e.g. updated
    audiences) updates that entry in place rather than duplicating it."""
    config_file = tmp_path / "config.yaml"
    current = _oidc_config(_issuer("https://idp.example.com", audiences=["old-aud"]))

    persist_guild_join(
        url="https://guild.example.com",
        node_id="node-1",
        current_host_auth=current,
        identity_trust={
            "mode": "oidc",
            "issuers": [{"issuer": "https://idp.example.com", "audiences": ["new-aud"]}],
        },
        config_file=config_file,
    )

    saved = yaml.safe_load(config_file.read_text())
    issuers = saved["host_auth"]["oidc"]["issuers"]
    assert len(issuers) == 1
    assert issuers[0]["audiences"] == ["new-aud"]


def test_refuses_an_unrecognized_mode_string(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"

    warning = persist_guild_join(
        url="https://guild.example.com",
        node_id="node-1",
        current_host_auth=AuthConfig(),
        identity_trust={"mode": "something-new", "issuers": []},
        config_file=config_file,
    )

    assert warning is not None
    assert "unrecognized" in warning.lower()
    saved = yaml.safe_load(config_file.read_text())
    assert saved["host_auth"]["mode"] == "none"  # unchanged default


def test_missing_mode_defaults_to_none_not_a_crash(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"

    warning = persist_guild_join(
        url="https://guild.example.com",
        node_id="node-1",
        current_host_auth=AuthConfig(),
        identity_trust={"issuers": []},
        config_file=config_file,
    )

    assert warning is None
    saved = yaml.safe_load(config_file.read_text())
    assert saved["host_auth"]["mode"] == "none"


def test_refuses_malformed_reported_issuers(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"

    warning = persist_guild_join(
        url="https://guild.example.com",
        node_id="node-1",
        current_host_auth=AuthConfig(),
        identity_trust={"mode": "oidc", "issuers": [{"not_an_issuer_field": True}]},
        config_file=config_file,
    )

    assert warning is not None
    saved = yaml.safe_load(config_file.read_text())
    # AuthConfig requires each issuer to have an issuer + audience -- since
    # OidcIssuerConfig defaults issuer/audiences, a garbage dict still
    # parses to an EMPTY issuer, which AuthConfig(mode=oidc) then rejects;
    # either way the file stays parseable and unchanged from the default.
    AuthConfig(**saved["host_auth"])


def test_result_always_round_trips_through_authconfig(tmp_path: Path) -> None:
    """Whatever gets written, the CLI's own settings loader must be able to
    read it back on the very next invocation."""
    config_file = tmp_path / "config.yaml"
    for identity_trust in [
        {"mode": "oidc", "issuers": [{"issuer": "https://a.example.com", "audiences": ["x"]}]},
        {"mode": "envoy", "issuers": [{"issuer": "https://b.example.com", "audiences": ["y"]}]},
        {"mode": "none", "issuers": []},
        {"mode": "bogus", "issuers": []},
        {},
    ]:
        persist_guild_join(
            url="https://guild.example.com",
            node_id="node-1",
            current_host_auth=AuthConfig(),
            identity_trust=identity_trust,
            config_file=config_file,
        )
        saved = yaml.safe_load(config_file.read_text())
        AuthConfig(**saved["host_auth"])  # never raises
