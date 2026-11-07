"""The compute configuration: registered hosts are refused, loudly, when they cannot be safe."""

import pytest
from pydantic import ValidationError

from volundr.compute.config import (
    ComputeConfig,
    ComputeHostsConfig,
    HostConnectorConfig,
    HostJoinListenerConfig,
)

CONNECTOR = {"adapter": "volundr.adapters.outbound.host_connectors.DirectSshConnector"}


def hosts(**update) -> dict:
    return {
        "join_url": "https://forge.example.test",
        "connectors": [{**CONNECTOR, "binding_id": "direct", "kwargs": {"allowed_sources": []}}],
        "reserved_cpu": 1,
        "reserved_memory_mib": 1024,
        "min_free_disk_mib": 2048,
        **update,
    }


# -- join_url -----------------------------------------------------------------------------------


def test_an_https_join_url_is_accepted_and_nothing_else_is_plaintext():
    config = ComputeHostsConfig(**hosts())

    assert config.join_url == "https://forge.example.test"
    assert config.allow_plaintext_join is False


def test_a_plaintext_join_url_is_refused_with_the_remedy():
    with pytest.raises(ValidationError, match="plaintext http") as refused:
        ComputeHostsConfig(**hosts(join_url="http://forge.example.test"))

    assert "allow_plaintext_join: true" in str(refused.value)
    assert "Use https" in str(refused.value)


def test_a_plaintext_join_url_is_accepted_when_the_operator_allows_it():
    config = ComputeHostsConfig(
        **hosts(join_url="http://forge.example.test:8080", allow_plaintext_join=True)
    )

    assert config.join_url == "http://forge.example.test:8080"


def test_allowing_plaintext_does_not_change_an_https_url():
    config = ComputeHostsConfig(**hosts(allow_plaintext_join=True))

    assert config.join_url.startswith("https://")


@pytest.mark.parametrize(
    "url",
    [
        "forge.example.test",
        "ftp://forge.example.test",
        "https://",
        "https://forge.example.test/path",
        "https://forge.example.test/",
        "https://forge.example.test?x=1",
        "https://forge.example.test#frag",
    ],
)
def test_a_join_url_is_a_scheme_and_host_with_no_path(url):
    with pytest.raises(ValidationError):
        ComputeHostsConfig(**hosts(join_url=url, allow_plaintext_join=True))


@pytest.mark.parametrize("url", ["https://forge.example.test x", "https://forge.test;rm"])
def test_a_join_url_host_may_not_hold_whitespace_or_shell_metacharacters(url):
    with pytest.raises(ValidationError):
        ComputeHostsConfig(**hosts(join_url=url))


# -- join listener ------------------------------------------------------------------------------


def test_without_a_join_url_forge_serves_the_pinned_listener_with_its_defaults():
    config = ComputeHostsConfig(**hosts(join_url=""))

    assert config.join_listener == HostJoinListenerConfig()
    assert config.join_listener.port == 8443
    assert config.join_listener.bind_host == "0.0.0.0"


def test_an_explicit_join_url_without_a_listener_keeps_forges_api_serving_the_join():
    config = ComputeHostsConfig(**hosts())

    assert config.join_listener is None
    assert config.join_base_url("ignored.example") == "https://forge.example.test"


def test_the_join_url_derives_from_the_address_hosts_reach():
    config = ComputeHostsConfig(**hosts(join_url="", join_listener={"port": 9443}))

    assert config.join_base_url("192.168.1.20") == "https://192.168.1.20:9443"
    assert config.join_base_url(" forge.lan ") == "https://forge.lan:9443"


def test_an_explicit_join_url_wins_over_the_derived_one_for_a_listener():
    config = ComputeHostsConfig(
        **hosts(join_url="https://forge.example.test:443", join_listener={})
    )

    assert config.join_base_url("192.168.1.20") == "https://forge.example.test:443"


def test_a_listener_never_joins_over_plaintext_even_when_plaintext_is_allowed():
    with pytest.raises(ValidationError, match="join_listener serves TLS only"):
        ComputeHostsConfig(
            **hosts(
                join_url="http://forge.example.test",
                join_listener={},
                allow_plaintext_join=True,
            )
        )


@pytest.mark.parametrize("public_host", ["127.0.0.1", "127.1.2.3", "localhost", "0.0.0.0"])
def test_an_address_no_other_machine_reaches_is_refused_with_the_remedy(public_host):
    config = ComputeHostsConfig(**hosts(join_url=""))

    with pytest.raises(ValueError, match="other machines cannot reach it") as refused:
        config.join_base_url(public_host)

    assert "server_public_host" in str(refused.value)
    assert "compute.hosts.join_url" in str(refused.value)


@pytest.mark.parametrize("public_host", ["", "::1", "fd00::1", "forge.lan;rm", "a b"])
def test_a_public_host_that_cannot_go_into_a_shell_url_is_refused(public_host):
    config = ComputeHostsConfig(**hosts(join_url=""))

    with pytest.raises(ValueError, match="must be a hostname or IPv4 address"):
        config.join_base_url(public_host)


def test_a_join_base_url_without_url_or_listener_is_refused():
    config = ComputeHostsConfig(**hosts())
    config.join_url = ""

    with pytest.raises(ValueError, match="needs join_url or join_listener"):
        config.join_base_url("192.168.1.20")


@pytest.mark.parametrize("update", [{"port": 0}, {"port": 65536}, {"key_bits": 1024}, {"x": 1}])
def test_listener_settings_are_bounded(update):
    with pytest.raises(ValidationError):
        HostJoinListenerConfig(**update)


# -- connectors ---------------------------------------------------------------------------------


def test_connector_binding_ids_must_be_unique():
    connectors = [
        {**CONNECTOR, "binding_id": "direct", "kwargs": {"allowed_sources": []}},
        {**CONNECTOR, "binding_id": "other"},
        {**CONNECTOR, "binding_id": "direct", "kwargs": {"allowed_sources": []}},
    ]

    with pytest.raises(ValidationError, match="binding_id values must be unique"):
        ComputeHostsConfig(**hosts(connectors=connectors))


def test_distinct_connector_binding_ids_are_accepted():
    connectors = [{**CONNECTOR, "binding_id": "direct"}, {**CONNECTOR, "binding_id": "relay-2"}]

    config = ComputeHostsConfig(**hosts(connectors=connectors))

    assert [connector.binding_id for connector in config.connectors] == ["direct", "relay-2"]


def test_at_least_one_connector_is_required():
    with pytest.raises(ValidationError):
        ComputeHostsConfig(**hosts(connectors=[]))


@pytest.mark.parametrize("binding_id", ["direct", "a", "0", "tail-scale-1", "a1-b2-c3"])
def test_a_connector_binding_id_is_a_lowercase_word_with_hyphens(binding_id):
    assert HostConnectorConfig(**CONNECTOR, binding_id=binding_id).binding_id == binding_id


@pytest.mark.parametrize(
    "binding_id",
    ["", "-direct", "Direct", "direct_ssh", "direct ssh", "direct.ssh", "direct/ssh", "é"],
)
def test_a_connector_binding_id_that_breaks_the_pattern_is_refused(binding_id):
    with pytest.raises(ValidationError):
        HostConnectorConfig(**CONNECTOR, binding_id=binding_id)


def test_a_connector_binding_id_with_a_trailing_newline_is_refused():
    with pytest.raises(ValidationError):
        HostConnectorConfig(**CONNECTOR, binding_id="direct\n")


# -- the rest of the section --------------------------------------------------------------------


@pytest.mark.parametrize("field", ["reserved_cpu", "reserved_memory_mib", "min_free_disk_mib"])
def test_the_host_reserve_and_disk_floor_have_no_default(field):
    values = hosts()
    del values[field]

    with pytest.raises(ValidationError, match=field):
        ComputeHostsConfig(**values)


@pytest.mark.parametrize("field", ["reserved_cpu", "reserved_memory_mib", "min_free_disk_mib"])
def test_the_host_reserve_and_disk_floor_may_not_be_negative(field):
    with pytest.raises(ValidationError):
        ComputeHostsConfig(**hosts(**{field: -1}))


@pytest.mark.parametrize(
    "field",
    [
        "pairing_code_ttl_seconds",
        "check_interval_seconds",
        "pending_check_interval_seconds",
        "command_timeout_seconds",
    ],
)
def test_every_interval_and_timeout_must_be_positive(field):
    with pytest.raises(ValidationError):
        ComputeHostsConfig(**hosts(**{field: 0}))


def test_unknown_keys_in_the_hosts_section_are_refused():
    with pytest.raises(ValidationError, match="extra"):
        ComputeHostsConfig(**hosts(join_urll="https://typo.example.test"))


def test_the_compute_config_holds_no_hosts_unless_configured():
    config = ComputeConfig(pool_id="pool", max_machines=1, provider={"adapter": "x.Y"})

    assert config.hosts is None


def test_the_compute_config_validates_its_hosts_section():
    with pytest.raises(ValidationError, match="plaintext http"):
        ComputeConfig(
            pool_id="pool",
            max_machines=1,
            provider={"adapter": "x.Y"},
            hosts=hosts(join_url="http://forge.example.test"),
        )

    config = ComputeConfig(
        pool_id="pool", max_machines=1, provider={"adapter": "x.Y"}, hosts=hosts()
    )

    assert config.hosts.connectors[0].binding_id == "direct"
