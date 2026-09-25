"""Pure LAN-transport policy for a URL a Guild instance may be dialled on.

Shared by the write-time registration validator (``services/instances.py``)
and the outbound call-time enforcement (``adapters/outbound/guild_transport.py``)
so both agree on exactly the same rule without either importing the other's
layer: an in-process embedded target or a loopback address never crosses a
network, so it is exempt; everything else needs ``https://`` unless the
instance's own config opts into plaintext.

The exemption is keyed on the URL's own scheme/host — never on a
user-editable flag such as ``config.transport`` — so a caller that dials a
URL directly (bypassing whatever routing convention reads that flag) gets the
same answer this policy would give anyone else.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

# Hosts exempt from the https-unless-allow_plaintext requirement: a loopback
# endpoint never leaves the machine, so there is no wire to harden.
LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})

# The scheme a same-process embedded target's base_url/ravn_base_url uses —
# dispatched over httpx.ASGITransport, never the network, so it is not a
# separate trust domain and this policy does not apply to it at all.
EMBEDDED_SCHEME = "embedded"

# Host suffixes exempt from the https-unless-allow_plaintext requirement,
# alongside LOCAL_HOSTNAMES — in-cluster Kubernetes service DNS
# (my-svc.my-namespace.svc.cluster.local) never leaves the cluster's pod
# network either, so a plaintext hop there carries the same "no wire to
# harden" reasoning as loopback, without every operator having to set
# config.allow_plaintext on every in-cluster seed. A module default,
# overridden once at process startup by the composition root via
# configure_trusted_plaintext_host_suffixes() from
# Settings.guild_transport_trusted_plaintext_host_suffixes — see
# guild_transport.py's own _connect_timeout_ceiling_seconds for the same
# pattern applied to timeouts.
_DEFAULT_TRUSTED_PLAINTEXT_HOST_SUFFIXES: tuple[str, ...] = (".svc.cluster.local", ".svc")
_trusted_plaintext_host_suffixes: tuple[str, ...] = _DEFAULT_TRUSTED_PLAINTEXT_HOST_SUFFIXES


def configure_trusted_plaintext_host_suffixes(suffixes: Iterable[str]) -> None:
    """Set the process-wide list of trusted-plaintext host suffixes.

    Called once by each composition root (``guild/app.py``) from
    ``Settings.guild_transport_trusted_plaintext_host_suffixes``. An empty
    iterable is a valid, deliberate choice — "require the explicit
    config.allow_plaintext opt-in everywhere, including in-cluster
    addresses" — not an error.
    """
    global _trusted_plaintext_host_suffixes
    _trusted_plaintext_host_suffixes = tuple(suffixes)


def default_trusted_plaintext_host_suffixes() -> tuple[str, ...]:
    """The current process-wide trusted-plaintext host suffix list."""
    return _trusted_plaintext_host_suffixes


def _normalize_suffix(suffix: str) -> str:
    """A configured suffix always anchors on a full DNS label boundary.

    ``.svc.cluster.local`` is written with its leading dot already; a
    suffix an operator wrote without one (``svc.cluster.local``) is
    corrected here rather than silently matching mid-label (which would let
    ``notreallyasvc.cluster.local`` pass as if it were a real in-cluster
    name).
    """
    normalized = suffix.strip().lower()
    if not normalized:
        return ""
    return normalized if normalized.startswith(".") else f".{normalized}"


def _matches_trusted_plaintext_suffix(hostname: str, suffixes: Iterable[str]) -> bool:
    """True when *hostname* ends with one of *suffixes* at a label boundary.

    Suffixes are matched with a leading dot (see ``_normalize_suffix``), so
    a plain ``str.endswith()`` is already anchored to a whole label:
    ``x.svc.cluster.local`` matches ``.svc.cluster.local``, but neither
    ``notreallyasvc.cluster.local`` (no dot immediately before ``svc``) nor
    ``evil-svc.cluster.local.example.com`` (its actual tail is
    ``.example.com``) does. Case-insensitive — ``urlsplit(...).hostname`` is
    already lowercased, and this normalizes independently of that — and
    tolerant of one trailing "." (an absolute FQDN is the same host).
    """
    normalized_host = hostname.rstrip(".").lower()
    for raw_suffix in suffixes:
        suffix = _normalize_suffix(raw_suffix)
        if suffix and normalized_host.endswith(suffix):
            return True
    return False


def normalize_allow_plaintext(config: dict) -> bool:
    """Validate and return ``config.allow_plaintext``, defaulting to ``False``.

    Must be an actual boolean — a truthy non-bool (``"true"``, ``1``) is a
    caller mistake, not a decision, and is rejected rather than silently
    coerced. Raises plain ``ValueError``; callers wrap it in whatever
    domain-specific exception type their layer uses.
    """
    value = config.get("allow_plaintext")
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise ValueError(f"config.allow_plaintext must be a boolean (true or false), got {value!r}")


def configured_dial_urls(base_url: str, config: dict) -> list[str]:
    """Every URL this instance's config says a Guild caller may dial.

    ``base_url`` is always one; a split-service target's ``ravn_base_url``
    (or the legacy ``ravnBaseUrl`` alias) is another when configured — see
    ``rest_ravn._ravn_base_url``, which is what a caller actually connects to
    for Ravn reads instead of ``base_url``.
    """
    urls = [base_url]
    ravn_base_url = config.get("ravn_base_url") or config.get("ravnBaseUrl")
    if ravn_base_url:
        urls.append(str(ravn_base_url).strip())
    return urls


def insecure_transport_reason(url: str, *, allow_plaintext: bool) -> str | None:
    """Return ``None`` when *url* meets the Guild LAN-transport policy.

    Otherwise returns the remedy message to raise with. *allow_plaintext*
    must already be the normalized, validated opt-in (see
    ``normalize_allow_plaintext`` above) — this function does not itself
    validate the flag's type.

    A hostname matching the process-wide trusted-plaintext suffix list (see
    ``configure_trusted_plaintext_host_suffixes``, default
    ``.svc.cluster.local``/``.svc``) is exempt the same way ``LOCAL_HOSTNAMES``
    is — in-cluster Kubernetes service DNS never leaves the cluster network,
    so it carries the same "no wire to harden" reasoning as loopback. This
    exemption never applies to TLS pinning: ``guild_transport.resolve_guild_ssl_context``
    requires ``https://`` for ``config.tls_fingerprint`` independently of this
    function, regardless of hostname.
    """
    parsed = urlsplit(url)
    if parsed.scheme == EMBEDDED_SCHEME:
        return None
    if parsed.hostname in LOCAL_HOSTNAMES:
        return None
    if parsed.scheme == "https":
        return None
    if allow_plaintext:
        return None
    if parsed.hostname and _matches_trusted_plaintext_suffix(
        parsed.hostname, _trusted_plaintext_host_suffixes
    ):
        return None
    return (
        f"{url} must use https:// for a remote Guild instance; set "
        "config.allow_plaintext: true only when the network path is already "
        "encrypted or otherwise trusted (e.g. a Tailscale tailnet), or add its "
        "host suffix to guild_transport_trusted_plaintext_host_suffixes "
        f"(currently {list(_trusted_plaintext_host_suffixes)!r}) for a whole class "
        "of trusted addresses such as in-cluster service DNS"
    )
