"""Forge's pinned join listener: key and certificate made once, the pin, and the listener."""

from __future__ import annotations

import asyncio
import shlex
import shutil
import socket
import ssl
import stat
import subprocess
from types import SimpleNamespace

import httpx
import pytest
from cryptography import x509

from niuu.domain.models import Principal
from tests.compute_host_fakes import (
    HOST_KEY,
    POOL,
    Connector,
    HostPool,
    HostRepository,
    Inspector,
    Issuer,
    SlotLedger,
    make_execution,
)
from volundr.adapters.inbound import host_join_listener as module
from volundr.adapters.inbound.host_join_listener import (
    HostJoinListener,
    HostJoinListenerError,
    JoinTls,
    create_host_join_app,
    load_or_create_join_tls,
    spki_pin,
)
from volundr.domain.services.compute_hosts import ComputeHostService

KEY_BITS = 2048
ADMIN = Principal(user_id="admin", email="a@x", tenant_id="tenant", roles=["volundr:admin"])
JOIN_SH = "/api/v1/forge/compute/hosts/join.sh"


def _tls(tmp_path, name: str = "join") -> JoinTls:
    return load_or_create_join_tls(
        cert_file=str(tmp_path / name / "cert.pem"),
        key_file=str(tmp_path / name / "key.pem"),
        key_bits=KEY_BITS,
    )


@pytest.fixture(scope="module")
def shared_tls(tmp_path_factory) -> JoinTls:
    return _tls(tmp_path_factory.mktemp("listener"))


def _service(pin: str) -> ComputeHostService:
    return ComputeHostService(
        pool_id=POOL,
        repository=(repository := HostRepository()),
        compute_repository=SlotLedger(),
        compute_pool=HostPool(repository),
        connectors={"direct-ssh": Connector()},
        inspector=Inspector(),
        executions={"host@1": make_execution()},
        token_issuer=Issuer(),
        join_url="https://forge.lan:8443",
        join_pin=pin,
        pairing_code_ttl_seconds=600,
        check_interval_seconds=300,
        pending_check_interval_seconds=5,
        reserved_cpu=4,
        reserved_memory_mib=8192,
    )


def _listener(tls: JoinTls, *, port: int = 0) -> HostJoinListener:
    return HostJoinListener(
        create_host_join_app(_service(tls.pin)), bind_host="127.0.0.1", port=port, tls=tls
    )


def _trusting(tls: JoinTls) -> ssl.SSLContext:
    """A client context that verifies the listener's own self-signed certificate.

    The certificate names no host: hosts trust it by its pin (``curl --pinnedpubkey``),
    so the chain is verified and the hostname is not.
    """
    context = ssl.create_default_context(cafile=str(tls.cert_file))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    return context


def _peer_pin(port: int, tls: JoinTls) -> str:
    context = _trusting(tls)
    with socket.create_connection(("127.0.0.1", port)) as raw:
        with context.wrap_socket(raw) as wrapped:
            der = wrapped.getpeercert(binary_form=True)
    return spki_pin(x509.load_der_x509_certificate(der))


# -- key, certificate and pin -------------------------------------------------------------------


class TestTlsMaterial:
    def test_key_and_certificate_are_made_once_private(self, tmp_path):
        tls = _tls(tmp_path)

        assert stat.S_IMODE(tls.key_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(tls.cert_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(tls.key_file.parent.stat().st_mode) == 0o700
        assert tls.pin.startswith("sha256//")
        assert len(tls.pin) == len("sha256//") + 44

    def test_a_restart_reuses_both_files_and_the_pin(self, tmp_path):
        first = _tls(tmp_path)
        key, cert = first.key_file.read_bytes(), first.cert_file.read_bytes()

        again = _tls(tmp_path)

        assert again.pin == first.pin
        assert again.key_file.read_bytes() == key
        assert again.cert_file.read_bytes() == cert

    def test_a_deleted_certificate_is_made_again_from_the_key_with_the_same_pin(self, tmp_path):
        first = _tls(tmp_path)
        first.cert_file.unlink()

        again = _tls(tmp_path)

        assert again.cert_file.exists()
        assert again.pin == first.pin

    def test_the_certificate_never_expires_and_is_no_ca(self, tmp_path):
        tls = _tls(tmp_path)
        certificate = x509.load_pem_x509_certificate(tls.cert_file.read_bytes())

        assert certificate.not_valid_after_utc.year == 9999
        assert certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is (
            False
        )

    def test_a_certificate_of_another_key_is_refused(self, tmp_path):
        mine, other = _tls(tmp_path, "mine"), _tls(tmp_path, "other")
        mine.cert_file.write_bytes(other.cert_file.read_bytes())

        with pytest.raises(HostJoinListenerError, match="does not belong to key_file"):
            _tls(tmp_path, "mine")

    def test_a_certificate_that_is_not_pem_is_refused(self, tmp_path):
        tls = _tls(tmp_path)
        tls.cert_file.write_bytes(b"not a certificate")

        with pytest.raises(HostJoinListenerError, match="is not a PEM certificate"):
            _tls(tmp_path)

    def test_a_certificate_others_can_read_is_refused(self, tmp_path):
        tls = _tls(tmp_path)
        tls.cert_file.chmod(0o644)

        with pytest.raises(HostJoinListenerError, match="readable by other users"):
            _tls(tmp_path)

    def test_an_unwritable_key_location_is_fatal_and_names_the_knob(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("")

        with pytest.raises(HostJoinListenerError, match="compute.hosts.join_listener") as refused:
            load_or_create_join_tls(
                cert_file=str(blocker / "cert.pem"),
                key_file=str(blocker / "key.pem"),
                key_bits=KEY_BITS,
            )

        assert "cannot create the directory" in str(refused.value)

    @pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")
    def test_the_pin_is_what_curl_computes_from_the_certificate(self, tmp_path):
        tls = _tls(tmp_path)
        public = subprocess.run(
            ["openssl", "x509", "-in", str(tls.cert_file), "-pubkey", "-noout"],
            capture_output=True,
            check=True,
        ).stdout
        der = subprocess.run(
            ["openssl", "pkey", "-pubin", "-outform", "der"],
            input=public,
            capture_output=True,
            check=True,
        ).stdout
        digest = subprocess.run(
            ["openssl", "dgst", "-sha256", "-binary"], input=der, capture_output=True, check=True
        ).stdout
        encoded = subprocess.run(
            ["openssl", "base64", "-A"], input=digest, capture_output=True, check=True
        ).stdout

        assert tls.pin == "sha256//" + encoded.decode().strip()


# -- the listener -------------------------------------------------------------------------------


class TestListener:
    async def test_serves_only_the_host_facing_routes_over_tls_with_the_pinned_key(
        self, shared_tls
    ):
        listener = _listener(shared_tls)
        await listener.start()
        try:
            assert listener.port != 0
            assert await asyncio.to_thread(_peer_pin, listener.port, shared_tls) == shared_tls.pin
            async with httpx.AsyncClient(
                base_url=f"https://127.0.0.1:{listener.port}", verify=_trusting(shared_tls)
            ) as client:
                assert (await client.get(JOIN_SH)).status_code == 401
                joined = await client.post(
                    JOIN_SH[: -len(".sh")], json={"name": "glad", "public_key": HOST_KEY}
                )
                assert joined.status_code == 401
                assert (await client.get("/api/v1/forge/compute/hosts")).status_code == 404
                assert (await client.get("/docs")).status_code == 404
                assert (await client.get("/openapi.json")).status_code == 404
        finally:
            await listener.stop()

    @pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")
    async def test_curl_reaches_it_with_the_pin_and_refuses_any_other(self, shared_tls):
        listener = _listener(shared_tls)
        await listener.start()
        url = f"https://127.0.0.1:{listener.port}{JOIN_SH}"

        def curl(pin: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["curl", "-sS", "-k", "--pinnedpubkey", pin, "-o", "/dev/null"]
                + ["-w", "%{http_code}", url],
                capture_output=True,
                text=True,
            )

        try:
            pinned = await asyncio.to_thread(curl, shared_tls.pin)
            other = await asyncio.to_thread(curl, "sha256//" + "A" * 43 + "=")
        finally:
            await listener.stop()

        assert pinned.returncode == 0, pinned.stderr
        assert pinned.stdout == "401"
        assert other.returncode == 90  # CURLE_SSL_PINNEDPUBKEYNOTMATCH
        assert other.stdout == "000"

    async def test_a_port_in_use_is_fatal_and_names_the_knob(self, shared_tls):
        with socket.create_server(("127.0.0.1", 0)) as taken:
            listener = _listener(shared_tls, port=taken.getsockname()[1])

            with pytest.raises(HostJoinListenerError, match="cannot listen on") as refused:
                await listener.start()

        assert "compute.hosts.join_listener.port" in str(refused.value)

    async def test_a_key_it_cannot_load_is_fatal(self, tmp_path):
        bad = tmp_path / "bad.pem"
        bad.write_text("garbage")
        listener = _listener(JoinTls(cert_file=bad, key_file=bad, pin="sha256//x="))

        with pytest.raises(HostJoinListenerError, match="cannot load"):
            await listener.start()

    async def test_a_server_that_fails_to_start_is_fatal(self, shared_tls, monkeypatch):
        async def failing_startup(self, sockets=None):
            raise OSError("no protocol")

        monkeypatch.setattr(module._EmbeddedServer, "startup", failing_startup)
        listener = _listener(shared_tls)

        with pytest.raises(HostJoinListenerError, match="failed to start.*no protocol"):
            await listener.start()

    async def test_stop_before_start_and_twice_is_harmless(self, shared_tls):
        listener = _listener(shared_tls)
        await listener.stop()
        await listener.start()
        await listener.stop()
        await listener.stop()

    async def test_a_listener_that_ends_on_its_own_is_logged_and_counted(
        self, shared_tls, monkeypatch, caplog
    ):
        counted: list[str] = []
        monkeypatch.setattr(
            module,
            "get_observability",
            lambda: SimpleNamespace(count=lambda name, *a, **k: counted.append(name)),
        )
        listener = _listener(shared_tls)
        await listener.start()
        task = listener._task
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

        assert counted == ["volundr.compute.hosts.join_listener_stopped"]
        assert "hosts cannot join until Forge restarts" in caplog.text
        await listener.stop()

    async def test_a_listener_stopped_by_forge_is_not_reported(
        self, shared_tls, monkeypatch, caplog
    ):
        counted: list[str] = []
        monkeypatch.setattr(
            module,
            "get_observability",
            lambda: SimpleNamespace(count=lambda name, *a, **k: counted.append(name)),
        )
        listener = _listener(shared_tls)
        await listener.start()
        await listener.stop()
        await asyncio.sleep(0)

        assert counted == []
        assert "stopped (" not in caplog.text


# -- a real code, minted and fetched the way an operator and a host do --------------------------


def _mini_settings(tmp_path):
    from volundr.config import Settings

    return Settings(
        compute={
            "pool_id": POOL,
            "max_machines": 1,
            "provider": {"adapter": "x.Y"},
            "hosts": {
                "connectors": [{"adapter": "x.Connector", "binding_id": "direct-ssh"}],
                "reserved_cpu": 1,
                "reserved_memory_mib": 1024,
                "min_free_disk_mib": 1024,
                "pairing_signing_key_file": str(tmp_path / "pairing.pem"),
            },
        }
    )


def _mini_signer(tmp_path):
    """The signer a mini-mode Forge composes: no workload identity, its own key file."""
    from niuu.service_runtime import create_workload_identity_service
    from volundr.composition_builders import _create_host_pairing_signer

    settings = _mini_settings(tmp_path)
    workload = create_workload_identity_service(settings.workload_identity)
    assert workload.enabled is False
    return _create_host_pairing_signer(settings, workload)


def _real_service(tls: JoinTls, signer, repository: HostRepository) -> ComputeHostService:
    return ComputeHostService(
        pool_id=POOL,
        repository=repository,
        compute_repository=SlotLedger(),
        compute_pool=HostPool(repository),
        connectors={"direct-ssh": Connector()},
        inspector=Inspector(),
        executions={"host@1": make_execution()},
        token_issuer=signer,
        join_url="https://127.0.0.1:{port}",
        join_pin=tls.pin,
        pairing_code_ttl_seconds=600,
        check_interval_seconds=300,
        pending_check_interval_seconds=5,
        reserved_cpu=4,
        reserved_memory_mib=8192,
    )


@pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")
class TestMintThenFetch:
    async def _mint(self, service: ComputeHostService):
        return await service.mint(
            ADMIN, execution="host@1", connector="direct-ssh", tags=[], slots=None
        )

    async def _fetch(self, tls, service, command: str, out) -> subprocess.CompletedProcess:
        """Run the minted download command against a listener serving ``service``."""
        listener = HostJoinListener(
            create_host_join_app(service), bind_host="127.0.0.1", port=0, tls=tls
        )
        await listener.start()
        try:
            argv = shlex.split(command.replace("{port}", str(listener.port)))
            argv[argv.index("-o") + 1] = str(out)
            return await asyncio.to_thread(
                subprocess.run, [*argv, "-w", "%{http_code}"], capture_output=True, text=True
            )
        finally:
            await listener.stop()

    async def test_the_minted_command_fetches_the_join_script(self, tmp_path, shared_tls):
        service = _real_service(shared_tls, _mini_signer(tmp_path), HostRepository())
        minted = await self._mint(service)

        fetched = await self._fetch(shared_tls, service, minted.command_download, tmp_path / "j")

        assert fetched.returncode == 0, fetched.stderr
        assert fetched.stdout == "200"
        script = (tmp_path / "j").read_text()
        assert script.startswith("#!")
        assert minted.code in script

    async def test_a_code_minted_before_a_restart_still_fetches_after_it(
        self, tmp_path, shared_tls
    ):
        repository = HostRepository()
        minted = await self._mint(_real_service(shared_tls, _mini_signer(tmp_path), repository))
        restarted = _real_service(shared_tls, _mini_signer(tmp_path), repository)

        fetched = await self._fetch(shared_tls, restarted, minted.command_download, tmp_path / "j")

        assert (fetched.returncode, fetched.stdout) == (0, "200"), fetched.stderr

    async def test_a_code_another_forge_signed_is_refused(self, tmp_path, shared_tls):
        repository = HostRepository()
        minted = await self._mint(
            _real_service(shared_tls, _mini_signer(tmp_path / "other"), repository)
        )
        ours = _real_service(shared_tls, _mini_signer(tmp_path), repository)

        fetched = await self._fetch(shared_tls, ours, minted.command_download, tmp_path / "j")

        assert fetched.stdout == "401"
        assert fetched.returncode == 22  # curl -f

    async def test_a_code_this_forge_has_no_record_of_is_refused(self, tmp_path, shared_tls):
        # A stack restarted on a fresh database keeps its key but not the code.
        signer = _mini_signer(tmp_path)
        minted = await self._mint(_real_service(shared_tls, signer, HostRepository()))
        fresh = _real_service(shared_tls, signer, HostRepository())

        fetched = await self._fetch(shared_tls, fresh, minted.command_download, tmp_path / "j")

        assert fetched.stdout == "401"
