"""Tests for the service discovery registry (NIU-521).

Test strategy
-------------
Unit tests mock ``os.kill`` and file I/O to run without real processes.
Functional tests use real files in a temporary directory (tmp_path fixture).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sleipnir.adapters.discovery import (
    DEFAULT_REGISTRY_PATH,
    ServiceEntry,
    ServiceRegistry,
    _is_alive,
)

# ---------------------------------------------------------------------------
# _is_alive unit tests
# ---------------------------------------------------------------------------


def test_is_alive_running_process():
    """Current process is always alive."""
    assert _is_alive(os.getpid()) is True


def test_is_alive_dead_pid():
    """A PID that doesn't exist returns False."""
    # PID 0 is never a normal process; on Linux os.kill(0, 0) sends to the
    # process group, which can raise PermissionError.  Use a patched approach.
    with patch("os.kill", side_effect=ProcessLookupError):
        assert _is_alive(99999) is False


def test_is_alive_permission_error_means_alive():
    """PermissionError means the process exists but belongs to another user."""
    with patch("os.kill", side_effect=PermissionError):
        assert _is_alive(1) is True


# ---------------------------------------------------------------------------
# ServiceRegistry functional tests (real files, tmp_path)
# ---------------------------------------------------------------------------


@pytest.fixture
def registry(tmp_path: Path) -> ServiceRegistry:
    return ServiceRegistry(path=tmp_path / "sleipnir.json")


def test_register_creates_file(registry: ServiceRegistry, tmp_path: Path):
    registry.register("svc-a", "ipc:///tmp/a.sock")
    assert (tmp_path / "sleipnir.json").exists()


def test_register_adds_entry(registry: ServiceRegistry):
    registry.register("svc-a", "ipc:///tmp/a.sock")
    entries = registry.list_services()
    assert len(entries) == 1
    assert entries[0].id == "svc-a"
    assert entries[0].socket == "ipc:///tmp/a.sock"
    assert entries[0].pid == os.getpid()


def test_register_multiple_services(registry: ServiceRegistry):
    registry.register("svc-a", "ipc:///tmp/a.sock")
    registry.register("svc-b", "ipc:///tmp/b.sock")
    entries = registry.list_services()
    ids = {e.id for e in entries}
    assert ids == {"svc-a", "svc-b"}


def test_register_replaces_existing_id(registry: ServiceRegistry):
    """Re-registering the same service_id updates the entry."""
    registry.register("svc-a", "ipc:///tmp/a.sock")
    registry.register("svc-a", "ipc:///tmp/a2.sock")
    entries = registry.list_services()
    assert len(entries) == 1
    assert entries[0].socket == "ipc:///tmp/a2.sock"


def test_deregister_removes_entry(registry: ServiceRegistry):
    registry.register("svc-a", "ipc:///tmp/a.sock")
    registry.deregister("svc-a")
    assert registry.list_services() == []


def test_deregister_nonexistent_is_noop(registry: ServiceRegistry):
    """Deregistering an entry that was never added should not raise."""
    registry.deregister("ghost")  # no exception


def test_deregister_missing_file_is_noop(registry: ServiceRegistry, tmp_path: Path):
    """Deregistering when the file doesn't exist should not raise."""
    assert not (tmp_path / "sleipnir.json").exists()
    registry.deregister("ghost")  # no exception


def test_list_services_missing_file(registry: ServiceRegistry, tmp_path: Path):
    """list_services returns [] when file doesn't exist."""
    assert not (tmp_path / "sleipnir.json").exists()
    assert registry.list_services() == []


def test_list_services_filters_dead_pids(registry: ServiceRegistry, tmp_path: Path):
    """Entries with dead PIDs are excluded from list_services."""
    # Write a fake entry with a non-existent PID directly.
    dead_pid = 999999
    data = {
        "services": [
            {"id": "dead-svc", "pid": dead_pid, "socket": "ipc:///tmp/dead.sock", "started": "x"},
            {
                "id": "live-svc",
                "pid": os.getpid(),
                "socket": "ipc:///tmp/live.sock",
                "started": "x",
            },
        ],
        "primary_pub": "",
    }
    (tmp_path / "sleipnir.json").write_text(json.dumps(data))

    with patch("sleipnir.adapters.discovery._is_alive") as mock_alive:
        mock_alive.side_effect = lambda pid: pid == os.getpid()
        entries = registry.list_services()

    assert len(entries) == 1
    assert entries[0].id == "live-svc"


def test_register_cleans_stale_on_write(registry: ServiceRegistry, tmp_path: Path):
    """register() prunes dead-PID entries when writing."""
    dead_pid = 999998
    data = {
        "services": [
            {"id": "dead-svc", "pid": dead_pid, "socket": "ipc:///tmp/dead.sock", "started": "x"},
        ],
        "primary_pub": "",
    }
    (tmp_path / "sleipnir.json").write_text(json.dumps(data))

    with patch("sleipnir.adapters.discovery._is_alive") as mock_alive:
        mock_alive.side_effect = lambda pid: pid == os.getpid()
        registry.register("svc-new", "ipc:///tmp/new.sock")
        entries = registry.list_services()

    ids = {e.id for e in entries}
    assert "dead-svc" not in ids
    assert "svc-new" in ids


def test_cleanup_stale_returns_count(registry: ServiceRegistry, tmp_path: Path):
    """cleanup_stale() returns the number of removed entries."""
    data = {
        "services": [
            {"id": "dead-1", "pid": 999991, "socket": "ipc:///tmp/d1.sock", "started": "x"},
            {"id": "dead-2", "pid": 999992, "socket": "ipc:///tmp/d2.sock", "started": "x"},
            {"id": "live-1", "pid": os.getpid(), "socket": "ipc:///tmp/live.sock", "started": "x"},
        ],
        "primary_pub": "",
    }
    (tmp_path / "sleipnir.json").write_text(json.dumps(data))

    with patch("sleipnir.adapters.discovery._is_alive") as mock_alive:
        mock_alive.side_effect = lambda pid: pid == os.getpid()
        removed = registry.cleanup_stale()

    assert removed == 2


def test_cleanup_stale_missing_file(registry: ServiceRegistry):
    """cleanup_stale() returns 0 when the file doesn't exist."""
    assert registry.cleanup_stale() == 0


def test_cleanup_stale_no_dead_entries(registry: ServiceRegistry):
    """cleanup_stale() returns 0 when nothing is stale."""
    registry.register("svc-a", "ipc:///tmp/a.sock")
    removed = registry.cleanup_stale()
    assert removed == 0


def test_registry_file_is_valid_json(registry: ServiceRegistry, tmp_path: Path):
    """The written registry file is valid JSON with the expected structure."""
    registry.register("svc-a", "ipc:///tmp/a.sock")
    data = json.loads((tmp_path / "sleipnir.json").read_text())
    assert "services" in data
    assert "primary_pub" in data
    assert len(data["services"]) == 1
    svc = data["services"][0]
    assert svc["id"] == "svc-a"
    assert svc["socket"] == "ipc:///tmp/a.sock"
    assert svc["pid"] == os.getpid()
    assert "started" in svc


def test_registry_creates_parent_directory(tmp_path: Path):
    """register() creates the registry's parent directory if it doesn't exist."""
    nested = tmp_path / "deep" / "nested" / "sleipnir.json"
    reg = ServiceRegistry(path=nested)
    reg.register("svc", "ipc:///tmp/s.sock")
    assert nested.exists()


def test_corrupt_registry_is_reset(registry: ServiceRegistry, tmp_path: Path):
    """A corrupt JSON file is silently reset to an empty registry."""
    (tmp_path / "sleipnir.json").write_text("{not valid json...")
    # Should not raise; should silently reset.
    entries = registry.list_services()
    assert entries == []


def test_corrupt_registry_reset_on_register(registry: ServiceRegistry, tmp_path: Path):
    """A corrupt JSON file is reset when a new service registers."""
    (tmp_path / "sleipnir.json").write_text("{bad json")
    registry.register("svc", "ipc:///tmp/s.sock")
    entries = registry.list_services()
    assert len(entries) == 1
    assert entries[0].id == "svc"


def test_primary_pub_stored_in_file(tmp_path: Path):
    """primary_pub is persisted in the registry file."""
    reg = ServiceRegistry(path=tmp_path / "r.json", primary_pub="ipc:///tmp/main.sock")
    reg.register("svc", "ipc:///tmp/s.sock")
    data = json.loads((tmp_path / "r.json").read_text())
    assert data["primary_pub"] == "ipc:///tmp/main.sock"


def test_default_registry_path():
    """DEFAULT_REGISTRY_PATH is under the home directory."""
    assert DEFAULT_REGISTRY_PATH == Path.home() / ".odin" / "sleipnir.json"


def test_service_entry_is_frozen():
    """ServiceEntry is an immutable frozen dataclass."""
    entry = ServiceEntry(id="x", pid=1, socket="ipc:///tmp/x.sock", started="t")
    with pytest.raises(Exception):
        entry.id = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# NngPublisher / NngSubscriber with discovery — unit tests (mocked pynng)
# ---------------------------------------------------------------------------

# These tests require pynng; skip them individually when it is not installed.
try:
    __import__("pynng")

    _pynng_available = True
except ImportError:
    _pynng_available = False

_skip_without_pynng = pytest.mark.skipif(not _pynng_available, reason="pynng not installed")


@pytest.fixture
def short_ipc(tmp_path: Path):
    """Return a factory that creates short IPC addresses safe for macOS.

    The pytest ``tmp_path`` hierarchy can exceed the 104-char Unix socket
    limit on macOS, so we create sockets under a short /tmp prefix instead.
    """
    import shutil
    import tempfile

    dirs: list[str] = []

    def _make(name: str = "s") -> str:
        d = tempfile.mkdtemp(prefix="disc_")
        dirs.append(d)
        return f"ipc://{d}/{name}.sock"

    yield _make

    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def mock_registry() -> MagicMock:
    reg = MagicMock(spec=ServiceRegistry)
    reg.list_services.return_value = []
    return reg


@_skip_without_pynng
@pytest.mark.asyncio
async def test_publisher_registers_on_start(short_ipc):
    """NngPublisher calls registry.register() after binding."""
    from sleipnir.adapters.nng_transport import NngPublisher

    address = short_ipc("pub")
    reg = MagicMock(spec=ServiceRegistry)

    async with NngPublisher(address=address, service_id="svc-a", registry=reg):
        reg.register.assert_called_once_with("svc-a", address)


@_skip_without_pynng
@pytest.mark.asyncio
async def test_publisher_deregisters_on_stop(short_ipc):
    """NngPublisher calls registry.deregister() on stop."""
    from sleipnir.adapters.nng_transport import NngPublisher

    address = short_ipc("pub_dereg")
    reg = MagicMock(spec=ServiceRegistry)

    async with NngPublisher(address=address, service_id="svc-a", registry=reg):
        pass

    reg.deregister.assert_called_once_with("svc-a")


@_skip_without_pynng
@pytest.mark.asyncio
async def test_publisher_no_registry_no_registration(short_ipc):
    """NngPublisher without registry does not attempt registration."""
    from sleipnir.adapters.nng_transport import NngPublisher

    address = short_ipc("pub_noreg")
    # No registry passed — should bind without any discovery call.
    async with NngPublisher(address=address):
        pass  # no AttributeError expected


@_skip_without_pynng
@pytest.mark.asyncio
async def test_subscriber_dials_discovered_addresses(short_ipc):
    """NngSubscriber with registry dials all discovered sockets."""
    from sleipnir.adapters.nng_transport import NngSubscriber

    address_a = short_ipc("disc_a")
    address_b = short_ipc("disc_b")

    # Start two publishers so the sockets exist.
    import pynng as _pynng

    pub_a = _pynng.Pub0()
    pub_b = _pynng.Pub0()
    try:
        pub_a.listen(address_a)
        pub_b.listen(address_b)

        reg = MagicMock(spec=ServiceRegistry)
        reg.list_services.return_value = [
            ServiceEntry(id="svc-a", pid=os.getpid(), socket=address_a, started="t"),
            ServiceEntry(id="svc-b", pid=os.getpid(), socket=address_b, started="t"),
        ]

        async with NngSubscriber(address=address_a, registry=reg, connect_settle_ms=50):
            reg.list_services.assert_called_once()
    finally:
        pub_a.close()
        pub_b.close()


@_skip_without_pynng
@pytest.mark.asyncio
async def test_subscriber_falls_back_to_address_when_registry_empty(short_ipc):
    """NngSubscriber falls back to address when registry returns no services."""
    from sleipnir.adapters.nng_transport import NngSubscriber

    address = short_ipc("fallback")

    import pynng as _pynng

    pub = _pynng.Pub0()
    try:
        pub.listen(address)

        reg = MagicMock(spec=ServiceRegistry)
        reg.list_services.return_value = []

        async with NngSubscriber(address=address, registry=reg, connect_settle_ms=50):
            reg.list_services.assert_called_once()
    finally:
        pub.close()


@_skip_without_pynng
@pytest.mark.asyncio
async def test_subscriber_no_registry_uses_address(short_ipc):
    """NngSubscriber without registry dials the single address (baseline)."""
    from sleipnir.adapters.nng_transport import NngSubscriber

    address = short_ipc("noreg")

    import pynng as _pynng

    pub = _pynng.Pub0()
    try:
        pub.listen(address)
        async with NngSubscriber(address=address, connect_settle_ms=50):
            pass
    finally:
        pub.close()


@_skip_without_pynng
@pytest.mark.asyncio
async def test_transport_passes_registry_to_both_components(short_ipc):
    """NngTransport propagates service_id and registry to publisher and subscriber."""
    from sleipnir.adapters.nng_transport import NngTransport

    address = short_ipc("transport")
    reg = MagicMock(spec=ServiceRegistry)
    reg.list_services.return_value = []

    async with NngTransport(address=address, service_id="svc-t", registry=reg):
        reg.register.assert_called_once_with("svc-t", address)
        reg.list_services.assert_called_once()

    reg.deregister.assert_called_once_with("svc-t")


# ---------------------------------------------------------------------------
# Functional: multi-process registry round-trip
# ---------------------------------------------------------------------------


def test_registry_round_trip_functional(tmp_path: Path):
    """Full register → list → deregister cycle with real files."""
    reg_path = tmp_path / "roundtrip.json"
    reg = ServiceRegistry(path=reg_path)

    reg.register("svc-a", "ipc:///tmp/a.sock")
    reg.register("svc-b", "ipc:///tmp/b.sock")

    entries = reg.list_services()
    assert {e.id for e in entries} == {"svc-a", "svc-b"}

    reg.deregister("svc-a")
    entries = reg.list_services()
    assert {e.id for e in entries} == {"svc-b"}

    reg.deregister("svc-b")
    assert reg.list_services() == []
