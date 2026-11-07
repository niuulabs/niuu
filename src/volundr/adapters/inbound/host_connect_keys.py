"""The connect endpoint's ``AuthorizedKeysCommand`` asks Forge here, on a pod-local socket.

The host connect endpoint (an OpenSSH sidecar of the Forge pod) runs its key command on
every connection; the command sends one JSON line ``{"user", "type", "key"}`` on this
Unix socket and gets one back, ``{"host_id", "line"}`` for a key Forge admits or
``{"host_id": null, "line": null}`` for any other. It is never an HTTP route: the socket is
in a volume only Forge and the endpoint mount.

See ``docs/design/bm-compute-hosts.md`` ("Connection") and ``.claude/rules/architecture.md``
("the host connect endpoint").
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from niuu.observability import get_observability
from volundr.domain.compute_hosts import EndpointKey

logger = logging.getLogger(__name__)

#: Answers for one key: the service's ``ComputeHostService.endpoint_key``.
EndpointKeyAnswer = Callable[..., Awaitable[EndpointKey | None]]


class HostConnectKeyServer:
    def __init__(self, *, socket_path: str, answer: EndpointKeyAnswer) -> None:
        self._path = Path(socket_path)
        self._answer = answer
        self._server: asyncio.Server | None = None

    @property
    def socket_path(self) -> str:
        return str(self._path)

    async def start(self) -> None:
        # A socket file left by the previous Forge process in this pod is stale.
        self._path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._handle, path=self._path)
        # The endpoint's key command runs as its own user; only the two containers that
        # mount this volume can reach the socket at all.
        os.chmod(self._path, 0o666)
        logger.info("Answering the host connect endpoint's key lookups on %s", self._path)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        self._path.unlink(missing_ok=True)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = json.loads(await reader.readline())
            answer = await self._answer(
                user=str(request["user"]), key_type=str(request["type"]), key=str(request["key"])
            )
            reply = {
                "host_id": str(answer.host_id) if answer else None,
                "line": answer.line if answer else None,
            }
            writer.write(json.dumps(reply).encode() + b"\n")
            await writer.drain()
        except Exception:
            # The key command gets no answer and sshd refuses the connection; the host
            # retries. Counted, so a broken lookup is seen before every host is out.
            get_observability().count("volundr.compute.host.connect_key_errors")
            logger.exception("Could not answer the host connect endpoint's key lookup")
        finally:
            writer.close()
            await writer.wait_closed()
