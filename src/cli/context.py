"""The active context: which Niuu host a command talks to, and as whom.

``local`` is built in and means this machine's own server. Other contexts come from
``contexts:`` in ``~/.niuu/config.yaml`` (``niuu context add``). An active context that
is not configured is an error, never a quiet switch to ``local``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cli.auth.credentials import CredentialStore, StoredTokens
from cli.config import LOCAL_CONTEXT, CLISettings

#: Bind addresses that mean "every interface"; a client connects to loopback instead.
_ANY_ADDRESS = frozenset({"", "0.0.0.0", "::"})


class ContextError(Exception):
    """The active context cannot be used as configured; the message names the remedy."""


@dataclass(frozen=True)
class ResolvedContext:
    name: str
    url: str
    forge: str = ""
    issuer: str = ""

    @property
    def local(self) -> bool:
        return self.name == LOCAL_CONTEXT


def local_url(settings: CLISettings) -> str:
    """This machine's own server, as a client reaches it."""
    host = settings.server.host
    if host in _ANY_ADDRESS:
        host = "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{settings.server.port}"


def contexts(settings: CLISettings) -> list[ResolvedContext]:
    """Every context, ``local`` first."""
    return [
        ResolvedContext(name=LOCAL_CONTEXT, url=local_url(settings)),
        *(
            ResolvedContext(name=name, url=item.url, forge=item.forge, issuer=item.issuer)
            for name, item in sorted(settings.contexts.items())
        ),
    ]


def find(settings: CLISettings, name: str) -> ResolvedContext:
    for context in contexts(settings):
        if context.name == name:
            return context
    known = ", ".join(c.name for c in contexts(settings))
    raise ContextError(
        f"Context {name!r} is not configured (contexts: {known}). Switch with "
        f"`niuu context use <name>`, or add it with `niuu context add {name} <url>`."
    )


def active(settings: CLISettings) -> ResolvedContext:
    return find(settings, settings.context)


def login_for(
    context: ResolvedContext, store: CredentialStore | None = None
) -> StoredTokens | None:
    """The stored `niuu login`, when there is one that belongs to this context.

    A context that names its issuer refuses a login from another issuer: sending that
    token would only earn a 401 that hides the cause.
    """
    tokens = (store or CredentialStore()).load()
    if tokens is None or not context.issuer:
        return tokens
    if tokens.issuer.rstrip("/") == context.issuer.rstrip("/"):
        return tokens
    raise ContextError(
        f"You are logged in to {tokens.issuer or 'an unknown issuer'}, but context "
        f"{context.name!r} uses {context.issuer}. Run `niuu login`."
    )


def refresher(tokens: StoredTokens | None, store: CredentialStore) -> Any:
    """A refresh for tokens that know their issuer and client, else none."""
    if not tokens or not tokens.refresh_token or not tokens.issuer or not tokens.client_id:
        return None
    from cli.auth.oidc import OIDCClient

    return OIDCClient(
        issuer=tokens.issuer, client_id=tokens.client_id, credential_store=store
    ).refresh
