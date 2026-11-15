"""How a memory layer maps onto gbrain: one source, and one client bound to it.

gbrain scopes every read and write by *source*: a client is bound to one source,
its writes land there, and its reads see only the sources it is granted. A memory
layer is therefore a gbrain source, and Mímir reaches it with a client of its own.

======================  ===========================================================
Layer namespace         gbrain source id (``[a-z0-9-]{1,32}``, no underscores)
======================  ===========================================================
``org``                 ``org``
``project-<key>``       ``p-`` + the first 30 hex digits of ``sha256(namespace)``
``run-<key>``           ``r-`` + the same
``session-<key>``       ``s-`` + the same
``_proposals/<layer>``  the source of ``<layer>``: proposals are pages of the layer,
``_decided/<layer>``    under the slug prefix ``_proposals/`` or ``_decided/``
======================  ===========================================================

A key is a UUID, so the namespace is longer than gbrain's 32-character source id.
The id is derived from the namespace, never chosen, and the source's display name
**is** the namespace, so a source always says which layer it is. A source whose id
is not the id its name maps to is not a layer of Mímir's and is never touched; a
layer whose id is already taken by a source with another name stops with an error
(120 bits of digest make that a corruption or a foreign source, not bad luck).

Clients and where their credentials live
----------------------------------------
Each layer has a gbrain OAuth client ``mimir-layer-<namespace>`` (``client_credentials``,
bound to the layer's source, scopes ``read write sources_admin``: ``sources_admin`` so
the layer's own client can remove its own source, which gbrain allows nothing else to
do). One more, ``mimir-provisioner`` (``sources_admin``), only creates sources, because
a client can be bound only to a source that exists.

Mímir stores **no layer credential**. The one secret it holds is gbrain's owner
bootstrap credential (``admin_token``). gbrain keeps the secret of every client it
registered in its own delivery journal and hands it back to the owner
(``POST /admin/api/recover-client``), and refuses a second registration of a name
(``409 duplicate_name``, naming the client). So any gateway replica, at any time, runs
the same two calls and gets the same client and secret: nothing is rotated, nothing
is written to a volume that another replica would have to share, and a restart loses
nothing. Access tokens are fetched with the client secret and cached in memory until
shortly before they expire.

Nothing is repaired silently. A source or client that cannot be created, found or used
raises with the remedy. A client that exists but is not bound to its layer's source
(gbrain unbinds a client whose source is removed, and an unbound client acts on the
``default`` source) is refused, not reused.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from mimir.adapters.gbrain_mcp import (
    GBrainMcpClient,
    GBrainTransportError,
    result_payload,
    text_result,
)
from mimir.memory.models import (
    SYSTEM_LAYER_KINDS,
    Layer,
    MemoryBackendError,
    MemoryStoreError,
    MemoryValidationError,
    RecordNotFoundError,
    RevisionConflictError,
)

logger = logging.getLogger(__name__)

_DIGEST_CHARS = 30  # "p-" + 30 hex digits = 32 characters, gbrain's source id limit
_HELD = ("_proposals/", "_decided/")
CLIENT_PREFIX = "mimir-layer-"
PROVISIONER = "mimir-provisioner"
LAYER_SCOPES = "read write sources_admin"
PROVISIONER_SCOPES = "sources_admin"
_DEFAULT_SOURCE = "default"
_ADMIN_SESSION = "gbrain_admin"
_NOT_FOUND_CODES = frozenset({"page_not_found", "not_found"})
_CONFLICT_CODES = frozenset({"revision_conflict", "revision_required"})


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Address:
    """Where a memory namespace lives: the layer's source, and a slug prefix inside it."""

    layer: str
    held: str = ""

    @property
    def source_id(self) -> str:
        return source_id_of(self.layer)


def source_id_of(layer_namespace: str) -> str:
    """The gbrain source id of a layer namespace (see the module table)."""
    if layer_namespace == "org":
        return "org"
    kind = layer_namespace.partition("-")[0]
    digest = hashlib.sha256(layer_namespace.encode()).hexdigest()[:_DIGEST_CHARS]
    return f"{kind[0]}-{digest}"


def parse_namespace(namespace: str) -> Address:
    """The address of a namespace: a layer of the org's system, or what it holds for it."""
    held = ""
    layer = namespace
    for prefix in _HELD:
        if namespace.startswith(prefix):
            held, layer = prefix, namespace[len(prefix) :]
    try:
        parsed = Layer.from_namespace(layer)
    except MemoryStoreError as exc:
        raise MemoryValidationError(f"{namespace!r} is not a memory namespace: {exc}") from exc
    if parsed.kind not in SYSTEM_LAYER_KINDS or parsed.namespace != layer:
        raise MemoryValidationError(
            f"{namespace!r} is not a memory namespace of an org's system "
            "(org, project-<id>, run-<id> or session-<id>, or their _proposals/ and _decided/)"
        )
    return Address(layer=layer, held=held)


# ---------------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------------


async def call_tool(mcp: GBrainMcpClient, name: str, arguments: dict[str, Any]) -> Any:
    """One gbrain tool call; its answer, or the typed error it failed with."""
    try:
        result = await mcp.call(name, arguments)
    except GBrainTransportError as exc:
        raise MemoryBackendError(
            f"{exc}. Check that the brain is running, and that its client for this layer "
            "is active (see the memory system's admin_token_file)"
        ) from exc
    payload = result_payload(result)
    error = payload if isinstance(payload, dict) and "error" in payload else None
    if not result.get("isError") and error is None:
        return payload
    code = str((error or {}).get("error") or "error")
    message = str((error or {}).get("message") or text_result(result))
    suggestion = str((error or {}).get("suggestion") or "")
    text = f"gbrain {name} failed ({code}): {message}" + (f" — {suggestion}" if suggestion else "")
    if code in _NOT_FOUND_CODES:
        raise RecordNotFoundError(text)
    if code in _CONFLICT_CODES:
        raise RevisionConflictError(text)
    raise MemoryBackendError(text)


# ---------------------------------------------------------------------------
# gbrain's owner API and OAuth token endpoint
# ---------------------------------------------------------------------------


class _Gbrain:
    """HTTP to gbrain's server root: the owner API (a cookie session) and ``/token``."""

    def __init__(self, base_url: str, admin_token: str, *, timeout_seconds: float) -> None:
        self._base = base_url.rstrip("/")
        self._admin_token = admin_token
        self._timeout = timeout_seconds
        self._http: httpx.AsyncClient | None = None
        self._cookie: str | None = None
        self._login_lock = asyncio.Lock()

    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    async def close(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
        self._http = None

    def _fail(self, what: str, exc: Exception) -> MemoryBackendError:
        return MemoryBackendError(
            f"gbrain {what} failed at {self._base}: {exc}. Check that the brain is running and "
            "that base_url is its server root (the MCP URL without /mcp)"
        )

    async def _login(self) -> str:
        async with self._login_lock:
            if self._cookie is not None:
                return self._cookie
            try:
                response = await self.http().post(
                    f"{self._base}/admin/login", json={"token": self._admin_token}
                )
            except httpx.HTTPError as exc:
                raise self._fail("admin login", exc) from exc
            if response.status_code != httpx.codes.OK:
                raise MemoryBackendError(
                    f"gbrain refused the admin login (HTTP {response.status_code}): "
                    f"{_message(response)}. admin_token / admin_token_file must be the owner "
                    "bootstrap credential of this gbrain (GBRAIN_ADMIN_BOOTSTRAP_TOKEN); an "
                    "OAuth token cannot administer it"
                )
            cookie = response.cookies.get(_ADMIN_SESSION)
            if not cookie:
                raise MemoryBackendError(
                    "gbrain accepted the admin login but set no admin session; check that "
                    f"{self._base} is the gbrain server and not a proxy that strips cookies"
                )
            self._cookie = f"{_ADMIN_SESSION}={cookie}"
            return self._cookie

    async def admin(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        expect: tuple[int, ...] = (httpx.codes.OK,),
    ) -> tuple[int, Any]:
        """One owner API call; ``(status, JSON)``. A status outside *expect* raises.

        The admin session expires (a day, and on every gbrain restart): a 401 logs in
        again and sends the same call once. That renews credentials; it is not a retry.
        """
        for attempt in range(2):
            cookie = await self._login()
            try:
                response = await self.http().request(
                    method, f"{self._base}{path}", json=body, headers={"Cookie": cookie}
                )
            except httpx.HTTPError as exc:
                raise self._fail(f"{method} {path}", exc) from exc
            if response.status_code == httpx.codes.UNAUTHORIZED and attempt == 0:
                self._cookie = None
                continue
            break
        if response.status_code not in expect:
            raise MemoryBackendError(
                f"gbrain {method} {path} answered HTTP {response.status_code}: {_message(response)}"
            )
        try:
            return response.status_code, response.json()
        except ValueError as exc:
            raise MemoryBackendError(
                f"gbrain {method} {path} answered without JSON: {response.text[:200]}"
            ) from exc

    async def token(self, client_id: str, client_secret: str) -> tuple[str, float]:
        """A client-credentials access token and its lifetime in seconds."""
        try:
            response = await self.http().post(
                f"{self._base}/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
        except httpx.HTTPError as exc:
            raise GBrainTransportError(f"gbrain token request failed: {exc}") from exc
        if response.status_code != httpx.codes.OK:
            raise GBrainTransportError(
                f"gbrain refused a token for client {client_id} (HTTP {response.status_code}): "
                f"{_message(response)}. The client may have been revoked; remove its layer's "
                "source or revoke it in gbrain, and let Mímir register it again"
            )
        try:
            body = response.json()
            return str(body["access_token"]), float(body["expires_in"])
        except (ValueError, KeyError, TypeError) as exc:
            raise GBrainTransportError(
                f"gbrain's token answer for client {client_id} has no access_token/expires_in"
            ) from exc


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error_description") or body.get("error"))
    return str(body)[:200]


class _Bearer:
    """A client's access token, fetched when none is held or it is about to expire."""

    def __init__(
        self, gbrain: _Gbrain, client_id: str, client_secret: str, *, refresh_margin: float
    ) -> None:
        self._gbrain = gbrain
        self._id = client_id
        self._secret = client_secret
        self._margin = refresh_margin
        self._token = ""
        self._until = 0.0
        self._lock = asyncio.Lock()

    async def __call__(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._until:
                return self._token
            token, lifetime = await self._gbrain.token(self._id, self._secret)
            self._token = token
            self._until = time.monotonic() + lifetime - self._margin
            return token


@dataclass
class _Handle:
    client_id: str
    mcp: GBrainMcpClient


# ---------------------------------------------------------------------------
# The layers of one gbrain
# ---------------------------------------------------------------------------


class GBrainLayers:
    """The sources and clients of one gbrain's layers.

    Args:
        mcp_url: gbrain's MCP endpoint, which every layer client calls.
        base_url: gbrain's server root, for ``/token`` and ``/admin/*``.
        admin_token: gbrain's owner bootstrap credential.
        timeout_seconds: HTTP timeout per call.
        token_refresh_margin_seconds: how long before a token expires a new one is fetched.
    """

    def __init__(
        self,
        *,
        mcp_url: str,
        base_url: str,
        admin_token: str,
        timeout_seconds: float,
        token_refresh_margin_seconds: float,
    ) -> None:
        self._mcp_url = mcp_url
        self._timeout = timeout_seconds
        self._margin = token_refresh_margin_seconds
        self._gbrain = _Gbrain(base_url, admin_token, timeout_seconds=timeout_seconds)
        self._handles: dict[str, _Handle] = {}
        self._provisioner: _Handle | None = None
        self._locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        handles = list(self._handles.values())
        if self._provisioner is not None:
            handles.append(self._provisioner)
        for handle in handles:
            await handle.mcp.close()
        self._handles.clear()
        self._provisioner = None
        await self._gbrain.close()

    # -- what exists -----------------------------------------------------

    async def _sources(self) -> dict[str, str]:
        """Every source of the brain, id to display name."""
        _, body = await self._gbrain.admin("GET", "/admin/api/sources")
        if not isinstance(body, list):
            raise MemoryBackendError(f"gbrain listed its sources without a list: {str(body)[:200]}")
        return {str(row["id"]): str(row.get("name") or "") for row in body if "id" in row}

    async def _clients(self) -> list[dict[str, Any]]:
        _, body = await self._gbrain.admin("GET", "/admin/api/clients")
        rows = body.get("clients") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            raise MemoryBackendError(f"gbrain listed its clients without a list: {str(body)[:200]}")
        return [row for row in rows if isinstance(row, dict)]

    async def layers(self) -> list[str]:
        """Every layer with a source, or with a client left over from a removed source."""
        found: dict[str, None] = {}
        for source_id, name in (await self._sources()).items():
            if self._is_layer_source(source_id, name):
                found[name] = None
        for row in await self._clients():
            name = str(row.get("client_name") or "")
            if row.get("status") == "active" and name.startswith(CLIENT_PREFIX):
                layer = name[len(CLIENT_PREFIX) :]
                if self._is_layer(layer):
                    found[layer] = None
        return list(found)

    @staticmethod
    def _is_layer(namespace: str) -> bool:
        try:
            return parse_namespace(namespace) == Address(layer=namespace)
        except MemoryValidationError:
            return False

    def _is_layer_source(self, source_id: str, name: str) -> bool:
        return self._is_layer(name) and source_id == source_id_of(name)

    # -- access ----------------------------------------------------------

    async def client(self, layer: str, *, create: bool) -> GBrainMcpClient | None:
        """The MCP client bound to *layer*'s source.

        With *create* the source and client are made when missing. Without it a layer
        that has no source is ``None``: a layer nobody wrote to is empty, not an error.
        """
        mcp, _ = await self._ensure(layer, create=create)
        return mcp

    async def create(self, layer: str) -> bool:
        """Make *layer* ready for use. True when its source did not exist before."""
        _, created = await self._ensure(layer, create=True)
        return created

    async def _ensure(self, layer: str, *, create: bool) -> tuple[GBrainMcpClient | None, bool]:
        handle = self._handles.get(layer)
        if handle is not None:
            return handle.mcp, False
        async with self._locks.setdefault(layer, asyncio.Lock()):
            handle = self._handles.get(layer)
            if handle is not None:
                return handle.mcp, False
            created = False
            if not await self._source_exists(layer):
                if not create:
                    return None, False
                created = await self._create_source(layer)
            handle = await self._open(layer)
            self._handles[layer] = handle
            return handle.mcp, created

    async def _source_exists(self, layer: str) -> bool:
        source_id = source_id_of(layer)
        name = (await self._sources()).get(source_id)
        if name is None:
            return False
        if name != layer:
            raise MemoryBackendError(
                f"gbrain source {source_id} is named {name!r}, but it is the id of layer "
                f"{layer!r}; a source that is not that layer holds the id. Rename or remove "
                "it in gbrain before using this layer"
            )
        return True

    async def _create_source(self, layer: str) -> bool:
        """Add *layer*'s source. False when another replica added it first."""
        source_id = source_id_of(layer)
        provisioner = await self._provision()
        try:
            await call_tool(
                provisioner.mcp,
                "sources_add",
                {"id": source_id, "name": layer, "federated": False},
            )
        except MemoryStoreError as exc:
            if await self._source_exists(layer):
                return False  # another gateway replica added it between our check and our add
            raise MemoryBackendError(
                f"the source {source_id} of layer {layer} could not be created: {exc}. Creating "
                "sources over MCP needs a gbrain without managed persistence; add the source "
                f"by hand with `gbrain sources add {source_id} --name {layer}`"
            ) from exc
        return True

    async def _provision(self) -> _Handle:
        if self._provisioner is None:
            self._provisioner = await self._open_client(
                PROVISIONER, PROVISIONER_SCOPES, _DEFAULT_SOURCE
            )
        return self._provisioner

    async def _open(self, layer: str) -> _Handle:
        return await self._open_client(f"{CLIENT_PREFIX}{layer}", LAYER_SCOPES, source_id_of(layer))

    async def _open_client(self, name: str, scopes: str, source_id: str) -> _Handle:
        """The client *name*, registered if new, with its secret as gbrain keeps it."""
        status, body = await self._gbrain.admin(
            "POST",
            "/admin/api/register-client",
            {
                "name": name,
                "source": source_id,
                "scopes": scopes,
                "grantTypes": ["client_credentials"],
                "tokenEndpointAuthMethod": "client_secret_post",
            },
            expect=(httpx.codes.OK, httpx.codes.CONFLICT),
        )
        existing = status == httpx.codes.CONFLICT
        client_id = str(body.get("client_id" if existing else "clientId") or "")
        if not client_id:
            raise MemoryBackendError(f"gbrain registered client {name} without naming it: {body}")
        if existing:
            await self._check_client(name, client_id, scopes, source_id)
        _, exported = await self._recover(name, client_id)
        secret = exported.get("clientSecret")
        if not isinstance(secret, str) or not secret:
            raise MemoryBackendError(
                f"gbrain exported client {name} without its secret; revoke the client in gbrain "
                "(POST /admin/api/revoke-client) and let Mímir register it again"
            )
        bearer = _Bearer(self._gbrain, client_id, secret, refresh_margin=self._margin)
        mcp = GBrainMcpClient(
            self._mcp_url,
            timeout_seconds=self._timeout,
            bearer=bearer,
            http=self._gbrain.http(),
        )
        return _Handle(client_id=client_id, mcp=mcp)

    async def _recover(self, name: str, client_id: str) -> tuple[int, dict[str, Any]]:
        try:
            status, body = await self._gbrain.admin(
                "POST", "/admin/api/recover-client", {"clientId": client_id}
            )
        except MemoryBackendError as exc:
            raise MemoryBackendError(
                f"the secret of gbrain client {name} cannot be recovered: {exc}. gbrain keeps it "
                "in its credential delivery journal; if that is gone, revoke the client "
                "(POST /admin/api/revoke-client) and let Mímir register it again"
            ) from exc
        return status, body if isinstance(body, dict) else {}

    async def _check_client(self, name: str, client_id: str, scopes: str, source_id: str) -> None:
        """An existing client is reused only if it still has the grant it was made with."""
        _, body = await self._gbrain.admin("GET", f"/admin/api/clients/{client_id}")
        grant = body.get("grant") if isinstance(body, dict) else None
        grant = grant if isinstance(grant, dict) else {}
        held = set(grant.get("scopes") or [])
        if grant.get("sourceId") != source_id or not set(scopes.split()) <= held:
            raise MemoryBackendError(
                f"gbrain client {name} ({client_id}) is bound to source {grant.get('sourceId')!r} "
                f"with scopes {sorted(held)}, not to {source_id!r} with {scopes!r}: a client "
                "whose source was removed acts on the default source. Revoke it "
                "(POST /admin/api/revoke-client) and let Mímir register it again"
            )

    # -- removal ---------------------------------------------------------

    async def remove(self, layer: str) -> int:
        """Remove *layer*'s source and revoke its client; the pages the source held.

        The client is revoked even when the source is already gone, so a removal that
        stopped half way finishes on the next call.
        """
        mcp = await self.client(layer, create=False)
        pages = 0
        if mcp is not None:
            result = await call_tool(
                mcp, "sources_remove", {"id": source_id_of(layer), "confirm_destructive": True}
            )
            pages = int(result.get("pages_deleted", 0)) if isinstance(result, dict) else 0
        await self._revoke(layer)
        return pages

    async def _revoke(self, layer: str) -> None:
        handle = self._handles.pop(layer, None)
        client_ids = [handle.client_id] if handle is not None else []
        if handle is not None:
            await handle.mcp.close()
        else:
            name = f"{CLIENT_PREFIX}{layer}"
            client_ids = [
                str(row["client_id"])
                for row in await self._clients()
                if row.get("client_name") == name and row.get("status") == "active"
            ]
        for client_id in client_ids:
            await self._gbrain.admin("POST", "/admin/api/revoke-client", {"clientId": client_id})
        logger.info("memory layer %s: gbrain source removed, client revoked", layer)
