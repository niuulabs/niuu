"""Pinning an image reference to the digest its registry serves."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from volundr.adapters.outbound.registry_digests import (
    ImageResolutionError,
    resolve_image_digest,
    split_reference,
)

MANIFEST = b'{"schemaVersion": 2, "manifests": []}'
DIGEST = "sha256:" + hashlib.sha256(MANIFEST).hexdigest()
TIMEOUT = 5.0


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _resolve(handler, reference="ghcr.io/niuulabs/skuld:1.2.3") -> str:
    async with _client(handler) as client:
        return await resolve_image_digest(reference, client=client, timeout_seconds=TIMEOUT)


# ── split_reference ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("ghcr.io/niuulabs/skuld:1.2.3", ("ghcr.io", "niuulabs/skuld", "1.2.3")),
        ("ghcr.io/a/b/c:tag", ("ghcr.io", "a/b/c", "tag")),
        ("localhost:5000/img:v1", ("localhost:5000", "img", "v1")),
        ("docker.io/nginx:1", ("registry-1.docker.io", "library/nginx", "1")),
        ("docker.io/niuu/skuld:1", ("registry-1.docker.io", "niuu/skuld", "1")),
    ],
)
def test_split_reference(reference, expected):
    assert split_reference(reference) == expected


def test_reference_without_a_registry_is_refused():
    with pytest.raises(ImageResolutionError, match="names no registry"):
        split_reference("skuld:1")


def test_reference_without_a_tag_is_refused():
    with pytest.raises(ImageResolutionError, match="neither a tag nor a digest"):
        split_reference("ghcr.io/niuulabs/skuld")


# ── resolve_image_digest ────────────────────────────────────────────────────


async def test_a_digest_reference_is_returned_as_it_is_without_a_request():
    reference = f"ghcr.io/niuulabs/skuld:1@sha256:{'a' * 64}"

    def handler(request):
        raise AssertionError("no request expected")

    assert await _resolve(handler, reference) == reference


async def test_a_public_tag_resolves_to_the_served_manifests_digest():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=MANIFEST, headers={"docker-content-digest": DIGEST})

    resolved = await _resolve(handler)

    assert resolved == f"ghcr.io/niuulabs/skuld:1.2.3@{DIGEST}"
    assert str(seen[0].url) == "https://ghcr.io/v2/niuulabs/skuld/manifests/1.2.3"
    assert "application/vnd.oci.image.index.v1+json" in seen[0].headers["accept"]
    assert "authorization" not in seen[0].headers


async def test_a_registry_that_names_no_digest_header_is_trusted_by_content():
    resolved = await _resolve(lambda request: httpx.Response(200, content=MANIFEST))

    assert resolved.endswith(f"@{DIGEST}")


async def test_a_401_is_answered_with_an_anonymous_bearer_token():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "auth.example.io":
            return httpx.Response(200, json={"token": "anon"})
        if request.headers.get("authorization") != "Bearer anon":
            challenge = (
                'Bearer realm="https://auth.example.io/token",'
                'service="registry.example.io",scope="repository:niuulabs/skuld:pull"'
            )
            return httpx.Response(401, headers={"www-authenticate": challenge})
        return httpx.Response(200, content=MANIFEST, headers={"docker-content-digest": DIGEST})

    resolved = await _resolve(handler)

    assert resolved == f"ghcr.io/niuulabs/skuld:1.2.3@{DIGEST}"
    token_request = requests[1]
    assert token_request.url.params["service"] == "registry.example.io"
    assert token_request.url.params["scope"] == "repository:niuulabs/skuld:pull"


async def test_a_token_answer_may_name_access_token_instead():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.example.io":
            return httpx.Response(200, json={"access_token": "other"})
        if request.headers.get("authorization") != "Bearer other":
            return httpx.Response(
                401, headers={"www-authenticate": 'Bearer realm="https://auth.example.io/t"'}
            )
        return httpx.Response(200, content=MANIFEST)

    assert (await _resolve(handler)).endswith(DIGEST)


async def test_a_refused_token_names_the_status():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.example.io":
            return httpx.Response(403)
        return httpx.Response(
            401, headers={"www-authenticate": 'Bearer realm="https://auth.example.io/t"'}
        )

    with pytest.raises(ImageResolutionError, match=r"refused an anonymous pull token \(HTTP 403"):
        await _resolve(handler)


async def test_a_non_bearer_challenge_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, headers={"www-authenticate": 'Basic realm="registry"'})

    with pytest.raises(ImageResolutionError, match="asks for no bearer token"):
        await _resolve(handler)


async def test_a_missing_challenge_is_refused():
    with pytest.raises(ImageResolutionError, match="asks for no bearer token"):
        await _resolve(lambda request: httpx.Response(401))


async def test_a_challenge_without_a_realm_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, headers={"www-authenticate": 'Bearer service="registry"'})

    with pytest.raises(ImageResolutionError, match="names no token realm"):
        await _resolve(handler)


async def test_a_token_answer_without_a_token_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.example.io":
            return httpx.Response(200, json={})
        return httpx.Response(
            401, headers={"www-authenticate": 'Bearer realm="https://auth.example.io/t"'}
        )

    with pytest.raises(ImageResolutionError, match="answered no pull token"):
        await _resolve(handler)


async def test_a_missing_tag_names_the_status():
    with pytest.raises(ImageResolutionError, match="HTTP 404 for ghcr.io/niuulabs/skuld:1.2.3"):
        await _resolve(lambda request: httpx.Response(404))


async def test_a_still_unauthorised_answer_after_the_token_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.example.io":
            return httpx.Response(200, json={"token": "anon"})
        return httpx.Response(
            401, headers={"www-authenticate": 'Bearer realm="https://auth.example.io/t"'}
        )

    with pytest.raises(ImageResolutionError, match="HTTP 401"):
        await _resolve(handler)


async def test_a_transport_error_names_the_reference():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    with pytest.raises(ImageResolutionError, match="Cannot reach the registry for ghcr.io"):
        await _resolve(handler)


async def test_a_digest_header_that_disagrees_with_the_content_is_refused():
    wrong = "sha256:" + "0" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=MANIFEST, headers={"docker-content-digest": wrong})

    with pytest.raises(ImageResolutionError, match="says .* is sha256:0+, but served"):
        await _resolve(handler)


async def test_docker_io_library_images_are_normalised():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=MANIFEST)

    resolved = await _resolve(handler, "docker.io/nginx:1")

    assert seen == ["https://registry-1.docker.io/v2/library/nginx/manifests/1"]
    assert resolved == f"docker.io/nginx:1@{DIGEST}"
