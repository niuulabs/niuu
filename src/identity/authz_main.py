"""Run the pod-local Cedar-backed Envoy authorization service."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

import grpc
import yaml
from envoy.service.auth.v3.external_auth_pb2_grpc import add_AuthorizationServicer_to_server

from identity.adapters.envoy_authz import EnvoyAuthorizationService
from identity.authz_config import AuthorizationGatewayConfig
from identity.ports import AuthorizationPort
from niuu.ports.identity import HeaderAuthenticationPort
from niuu.utils import import_class


async def serve(config: AuthorizationGatewayConfig) -> None:
    authorization = import_class(config.adapter)(**config.kwargs)
    if not isinstance(authorization, AuthorizationPort):
        raise TypeError("Gateway adapter must implement AuthorizationPort")
    identity = None
    if config.identity is not None:
        identity = import_class(config.identity.adapter)(**config.identity.kwargs)
        if not isinstance(identity, HeaderAuthenticationPort):
            raise TypeError("Gateway identity adapter must implement HeaderAuthenticationPort")
    server = grpc.aio.server()
    add_AuthorizationServicer_to_server(
        EnvoyAuthorizationService(authorization, config, identity=identity), server
    )
    # Never expose trusted Envoy metadata to callers outside this pod.
    if not server.add_insecure_port(f"127.0.0.1:{config.port}"):
        raise RuntimeError("Cannot bind authorization gateway loopback listener")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await server.start()
    try:
        await stop.wait()
    finally:
        await server.stop(config.shutdown_grace_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = AuthorizationGatewayConfig.model_validate(yaml.safe_load(args.config.read_text()))
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve(config))


if __name__ == "__main__":
    main()
