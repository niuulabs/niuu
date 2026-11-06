from __future__ import annotations

import base64
import hashlib
import json
import stat
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from tests.test_guest_agent.conftest import ALLOCATION_ID
from volundr.guest_agent.config import BearerFileAuth, HostKeyAuth, SpiffeAuth
from volundr.guest_agent.errors import SignerError
from volundr.guest_agent.signers import (
    HEADER_ALLOCATION,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    BearerFileSigner,
    HostKeySigner,
    SpiffeSigner,
    build_signer,
    parse_jwt_svid_output,
    signing_input,
)

PATH = "/api/v1/internal/compute/metadata/credentials"


def test_signing_input_is_method_path_timestamp_and_body_hash() -> None:
    body = b'{"allocation_id": "x"}'

    expected = f"POST\n{PATH}\n1700000000123\n{hashlib.sha256(body).hexdigest()}".encode()

    assert signing_input("POST", PATH, 1700000000123, body) == expected


async def test_host_key_signature_verifies_with_the_public_key(
    host_key: tuple[str, Ed25519PublicKey],
) -> None:
    key_path, public_key = host_key
    body = b'{"allocation_id": "' + ALLOCATION_ID.encode() + b'"}'
    signer = HostKeySigner(
        key_path=key_path, allocation_id=ALLOCATION_ID, clock_ms=lambda: 1_700_000_000_123
    )

    headers = await signer.headers(method="POST", path=PATH, body=body)

    assert headers[HEADER_ALLOCATION] == ALLOCATION_ID
    assert headers[HEADER_TIMESTAMP] == "1700000000123"
    signature = base64.b64decode(headers[HEADER_SIGNATURE], validate=True)
    assert len(signature) == 64
    # Raises InvalidSignature if the signed bytes differ from the contract's.
    public_key.verify(signature, signing_input("POST", PATH, 1_700_000_000_123, body))


async def test_host_key_signature_covers_the_body(
    host_key: tuple[str, Ed25519PublicKey],
) -> None:
    key_path, public_key = host_key
    signer = HostKeySigner(key_path=key_path, allocation_id=ALLOCATION_ID, clock_ms=lambda: 5)

    headers = await signer.headers(method="POST", path=PATH, body=b"one")

    signature = base64.b64decode(headers[HEADER_SIGNATURE])
    with pytest.raises(InvalidSignature):
        public_key.verify(signature, signing_input("POST", PATH, 5, b"two"))


async def test_host_key_timestamp_strictly_increases_within_one_millisecond(
    host_key: tuple[str, Ed25519PublicKey],
) -> None:
    signer = HostKeySigner(key_path=host_key[0], allocation_id=ALLOCATION_ID, clock_ms=lambda: 1000)

    first = await signer.headers(method="POST", path=PATH, body=b"")
    second = await signer.headers(method="POST", path=PATH, body=b"")

    assert int(second[HEADER_TIMESTAMP]) > int(first[HEADER_TIMESTAMP])


async def test_host_key_timestamp_defaults_to_wall_clock_milliseconds(
    host_key: tuple[str, Ed25519PublicKey],
) -> None:
    signer = HostKeySigner(key_path=host_key[0], allocation_id=ALLOCATION_ID)

    headers = await signer.headers(method="POST", path=PATH, body=b"")

    assert len(headers[HEADER_TIMESTAMP]) == 13


def test_host_key_missing_file_is_named(tmp_path: Path) -> None:
    with pytest.raises(SignerError, match="cannot read auth.key_path"):
        HostKeySigner(key_path=str(tmp_path / "absent"), allocation_id=ALLOCATION_ID)


def test_host_key_garbage_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "key"
    path.write_text("not a key")

    with pytest.raises(SignerError, match="not an unencrypted OpenSSH private key"):
        HostKeySigner(key_path=str(path), allocation_id=ALLOCATION_ID)


def test_host_key_must_be_ed25519(tmp_path: Path) -> None:
    path = tmp_path / "key"
    path.write_bytes(
        rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption()
        )
    )

    with pytest.raises(SignerError, match="not an Ed25519 key"):
        HostKeySigner(key_path=str(path), allocation_id=ALLOCATION_ID)


def test_build_signer_selects_by_auth_kind(host_key: tuple[str, Ed25519PublicKey]) -> None:
    host = build_signer(
        HostKeyAuth(key_path=host_key[0]), allocation_id=ALLOCATION_ID, timeout_seconds=5
    )
    spiffe = build_signer(
        SpiffeAuth(audience="forge", socket_path="/s", spire_agent_binary="/b"),
        allocation_id=ALLOCATION_ID,
        timeout_seconds=5,
    )
    bearer = build_signer(
        BearerFileAuth(token_path="/t"), allocation_id=ALLOCATION_ID, timeout_seconds=5
    )

    assert isinstance(host, HostKeySigner)
    assert isinstance(spiffe, SpiffeSigner)
    assert isinstance(bearer, BearerFileSigner)


async def test_bearer_file_signer_rereads_the_rotated_token(tmp_path) -> None:
    token = tmp_path / "token"
    token.write_text("first\n")
    signer = BearerFileSigner(token_path=str(token))

    first = await signer.headers(method="POST", path=PATH, body=b"{}")
    token.write_text("second")
    second = await signer.headers(method="POST", path=PATH, body=b"{}")

    assert first == {"Authorization": "Bearer first"}
    assert second == {"Authorization": "Bearer second"}


@pytest.mark.parametrize(("content", "message"), [(None, "cannot read"), ("  \n", "is empty")])
async def test_bearer_file_signer_refuses_a_missing_or_empty_token(
    tmp_path, content: str | None, message: str
) -> None:
    token = tmp_path / "token"
    if content is not None:
        token.write_text(content)

    with pytest.raises(SignerError, match=message):
        await BearerFileSigner(token_path=str(token)).headers(method="POST", path=PATH, body=b"")


# --- SPIFFE output parsing ------------------------------------------------------------------

SVID_RESPONSE = {
    "svids": [{"spiffe_id": "spiffe://td/forge-vm/p/a", "svid": "jwt.one", "hint": ""}]
}
BUNDLES_RESPONSE = {"bundles": {"spiffe://td": "eyJrZXlzIjpbXX0="}}


def encode(document: object) -> bytes:
    return json.dumps(document).encode()


def test_parse_the_array_spire_prints_for_two_messages() -> None:
    # fetch_jwt.go prints PrintProto(svidResp, bundlesResp); protojson emits an array.
    assert parse_jwt_svid_output(encode([SVID_RESPONSE, BUNDLES_RESPONSE])) == "jwt.one"


def test_parse_a_single_object() -> None:
    assert parse_jwt_svid_output(encode(SVID_RESPONSE)) == "jwt.one"


def test_parse_finds_the_svids_message_wherever_it_is() -> None:
    assert parse_jwt_svid_output(encode([BUNDLES_RESPONSE, SVID_RESPONSE])) == "jwt.one"


def test_parse_takes_the_first_svid() -> None:
    document = {"svids": [{"svid": "first"}, {"svid": "second"}]}

    assert parse_jwt_svid_output(encode([document, BUNDLES_RESPONSE])) == "first"


@pytest.mark.parametrize(
    ("output", "message"),
    [
        (b"", "not JSON"),
        (b"token(spiffe://x):\n\tjwt", "not JSON"),
        (encode([BUNDLES_RESPONSE]), "without an 'svids' list"),
        (encode({"svids": []}), "returned no JWT-SVID"),
        (encode({"svids": None}), "returned no JWT-SVID"),
        (encode({"svids": [{"spiffe_id": "x", "svid": ""}]}), "empty JWT-SVID"),
        (encode({"svids": [{"spiffe_id": "x", "svid": "  "}]}), "empty JWT-SVID"),
        (encode({"svids": [{"spiffe_id": "x"}]}), "empty JWT-SVID"),
        (encode({"svids": ["jwt"]}), "empty JWT-SVID"),
        (encode({"error": "no identity issued"}), "reported: no identity issued"),
        (encode("jwt"), "without an 'svids' list"),
    ],
)
def test_parse_failures_raise(output: bytes, message: str) -> None:
    with pytest.raises(SignerError, match=message):
        parse_jwt_svid_output(output)


def test_parse_failure_never_echoes_the_output() -> None:
    secret = "eyJhbGciOiJSUzI1NiJ9.secret-payload.signature"

    with pytest.raises(SignerError) as raised:
        parse_jwt_svid_output(f"{secret} not json".encode())

    assert "secret-payload" not in str(raised.value)


# --- SPIFFE subprocess ----------------------------------------------------------------------


def make_script(path: Path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def spiffe_signer(binary: str, timeout: float = 5) -> SpiffeSigner:
    return SpiffeSigner(
        audience="forge-metadata",
        socket_path="/run/spire/agent/api.sock",
        spire_agent_binary=binary,
        timeout_seconds=timeout,
    )


async def test_spiffe_runs_the_documented_command_and_sends_a_bearer(tmp_path: Path) -> None:
    args_file = tmp_path / "args"
    output = tmp_path / "out.json"
    output.write_text(encode([SVID_RESPONSE, BUNDLES_RESPONSE]).decode())
    binary = make_script(tmp_path / "spire-agent", f'echo "$@" > {args_file}\ncat {output}\n')

    headers = await spiffe_signer(binary).headers(method="POST", path=PATH, body=b"{}")

    assert headers == {"Authorization": "Bearer jwt.one"}
    assert args_file.read_text().split() == [
        "api",
        "fetch",
        "jwt",
        "-audience",
        "forge-metadata",
        "-socketPath",
        "/run/spire/agent/api.sock",
        "-output",
        "json",
    ]


async def test_spiffe_fetches_a_fresh_svid_for_every_request(tmp_path: Path) -> None:
    counter = tmp_path / "count"
    counter.write_text("0")
    binary = make_script(
        tmp_path / "spire-agent",
        f"""n=$(($(cat {counter}) + 1))
echo $n > {counter}
echo '[{{"svids":[{{"spiffe_id":"x","svid":"jwt.'$n'"}}]}},{{"bundles":{{}}}}]'
""",
    )
    signer = spiffe_signer(binary)

    first = await signer.headers(method="POST", path=PATH, body=b"")
    second = await signer.headers(method="POST", path=PATH, body=b"")

    assert first["Authorization"] == "Bearer jwt.1"
    assert second["Authorization"] == "Bearer jwt.2"


async def test_spiffe_failure_summarizes_stderr(tmp_path: Path) -> None:
    binary = make_script(
        tmp_path / "spire-agent",
        "echo 'time=2026 level=warn' >&2\n"
        "echo 'Error: could not connect: connection refused' >&2\nexit 1\n",
    )

    with pytest.raises(SignerError, match="status 1: Error: could not connect: connection refused"):
        await spiffe_signer(binary).headers(method="POST", path=PATH, body=b"")


async def test_spiffe_failure_falls_back_to_the_json_error(tmp_path: Path) -> None:
    binary = make_script(
        tmp_path / "spire-agent", 'echo \'{"error": "workload unregistered"}\'\nexit 1\n'
    )

    with pytest.raises(SignerError, match="status 1: workload unregistered"):
        await spiffe_signer(binary).headers(method="POST", path=PATH, body=b"")


async def test_spiffe_failure_without_output_says_so(tmp_path: Path) -> None:
    binary = make_script(tmp_path / "spire-agent", "exit 3\n")

    with pytest.raises(SignerError, match="status 3: no error output"):
        await spiffe_signer(binary).headers(method="POST", path=PATH, body=b"")


async def test_spiffe_empty_svid_is_never_sent_as_a_bearer(tmp_path: Path) -> None:
    binary = make_script(
        tmp_path / "spire-agent", 'echo \'[{"svids":[{"svid":""}]},{"bundles":{}}]\'\n'
    )

    with pytest.raises(SignerError, match="empty JWT-SVID"):
        await spiffe_signer(binary).headers(method="POST", path=PATH, body=b"")


async def test_spiffe_timeout_kills_the_process_and_names_the_knob(tmp_path: Path) -> None:
    binary = make_script(tmp_path / "spire-agent", "exec sleep 30\n")

    with pytest.raises(SignerError, match="request_timeout_seconds=0.2"):
        await spiffe_signer(binary, timeout=0.2).headers(method="POST", path=PATH, body=b"")


async def test_spiffe_missing_binary_names_the_field(tmp_path: Path) -> None:
    with pytest.raises(SignerError, match="auth.spire_agent_binary"):
        await spiffe_signer(str(tmp_path / "absent")).headers(method="POST", path=PATH, body=b"")
