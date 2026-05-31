"""Helpers for constructing validated remote service URLs."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def build_remote_url(base_url: str, prefix: str, path: str) -> str:
    """Construct a remote URL from a validated base and a relative API path."""
    parsed_base = urlsplit(base_url)
    if parsed_base.scheme not in {"http", "https"}:
        raise ValueError("remote base URL must use http or https")
    if not parsed_base.netloc:
        raise ValueError("remote base URL must include a host")
    if parsed_base.username or parsed_base.password:
        raise ValueError("remote base URL must not embed credentials")
    if parsed_base.query or parsed_base.fragment:
        raise ValueError("remote base URL must not include query or fragment components")

    parsed_path = urlsplit(path)
    if parsed_path.scheme or parsed_path.netloc:
        raise ValueError("remote path must be relative")
    if not parsed_path.path.startswith("/"):
        raise ValueError("remote path must start with '/'")

    prefix_path = prefix.rstrip("/")
    base_path = parsed_base.path.rstrip("/")
    full_path = f"{base_path}{prefix_path}{parsed_path.path}"
    return urlunsplit(
        (
            parsed_base.scheme,
            parsed_base.netloc,
            full_path,
            parsed_path.query,
            "",
        )
    )
