"""Smoke-test the real standalone authorization process and graceful shutdown."""

import asyncio
import socket
import subprocess
import sys

import grpc
import yaml
from envoy.service.auth.v3 import external_auth_pb2, external_auth_pb2_grpc


async def test_authorization_process_serves_and_stops(tmp_path):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    config = tmp_path / "authz.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "port": port,
                "providers": [{"issuer": "https://issuer.test", "audiences": ["forge"]}],
                "routes": [{"path": "/api/v1/forge", "prefix": True, "methods": ["GET"]}],
            }
        )
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "identity.authz_main", "--config", str(config)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            await asyncio.wait_for(channel.channel_ready(), timeout=10)
            response = await external_auth_pb2_grpc.AuthorizationStub(channel).Check(
                external_auth_pb2.CheckRequest(),
                timeout=2,
            )
            assert response.denied_response.status.code == 401
        process.terminate()
        assert await asyncio.to_thread(process.wait, timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
            await asyncio.to_thread(process.wait, timeout=10)
        process.communicate()
