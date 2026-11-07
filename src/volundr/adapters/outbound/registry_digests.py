"""Resolve an image reference to the digest its registry serves, with an anonymous pull.

Forge pins the release's session images (``compute.session_images``) by digest once, at
startup, so every host runs exactly the same image and a moved tag never reaches a
running session. A reference that already names a digest is returned as it is. A
registry that cannot be reached, refuses an anonymous token or has no such tag stops
startup with the reference and the reason.
"""

from __future__ import annotations

import hashlib
import re

import httpx

#: What a manifest GET accepts: image indexes, image manifests and OCI artifacts.
_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
_DIGEST = re.compile(r"@sha256:[a-f0-9]{64}$")
_CHALLENGE_FIELD = re.compile(r'(\w+)="([^"]*)"')


class ImageResolutionError(RuntimeError):
    """An image reference could not be pinned to a digest."""


def split_reference(reference: str) -> tuple[str, str, str]:
    """``(registry, repository, tag)`` of a tag reference, as a registry API names them."""
    head, _, last = reference.rpartition("/")
    if not head:
        raise ImageResolutionError(f"Image reference {reference!r} names no registry")
    repository_last, _, tag = last.partition(":")
    if not tag:
        raise ImageResolutionError(
            f"Image reference {reference!r} names neither a tag nor a digest"
        )
    registry, _, path = f"{head}/{repository_last}".partition("/")
    if registry == "docker.io":
        registry = "registry-1.docker.io"
        if "/" not in path:
            path = f"library/{path}"
    return registry, path, tag


async def resolve_image_digest(
    reference: str, *, client: httpx.AsyncClient, timeout_seconds: float
) -> str:
    """``reference`` as ``registry/repository:tag@sha256:<digest>``."""
    if _DIGEST.search(reference):
        return reference
    registry, repository, tag = split_reference(reference)
    url = f"https://{registry}/v2/{repository}/manifests/{tag}"
    headers = {"Accept": _ACCEPT}
    try:
        response = await client.get(url, headers=headers, timeout=timeout_seconds)
        if response.status_code == 401:
            token = await _anonymous_token(
                response.headers.get("www-authenticate", ""), reference, client, timeout_seconds
            )
            headers["Authorization"] = f"Bearer {token}"
            response = await client.get(url, headers=headers, timeout=timeout_seconds)
    except httpx.HTTPError as exc:
        raise ImageResolutionError(f"Cannot reach the registry for {reference}: {exc}") from exc
    if response.status_code != 200:
        raise ImageResolutionError(
            f"The registry answered HTTP {response.status_code} for {reference}; the tag must "
            "exist and the image be public"
        )
    digest = "sha256:" + hashlib.sha256(response.content).hexdigest()
    served = response.headers.get("docker-content-digest")
    if served and served != digest:
        raise ImageResolutionError(
            f"The registry says {reference} is {served}, but served a manifest with {digest}"
        )
    return f"{reference}@{digest}"


async def _anonymous_token(
    challenge: str, reference: str, client: httpx.AsyncClient, timeout_seconds: float
) -> str:
    if not challenge.lower().startswith("bearer "):
        raise ImageResolutionError(
            f"The registry of {reference} asks for no bearer token ({challenge!r}); session "
            "images must be public"
        )
    fields = dict(_CHALLENGE_FIELD.findall(challenge))
    if "realm" not in fields:
        raise ImageResolutionError(f"The registry of {reference} names no token realm")
    params = {key: fields[key] for key in ("service", "scope") if key in fields}
    response = await client.get(fields["realm"], params=params, timeout=timeout_seconds)
    if response.status_code != 200:
        raise ImageResolutionError(
            f"The registry of {reference} refused an anonymous pull token (HTTP "
            f"{response.status_code}); session images must be public"
        )
    answer = response.json()
    token = answer.get("token") or answer.get("access_token")
    if not token:
        raise ImageResolutionError(f"The registry of {reference} answered no pull token")
    return token
