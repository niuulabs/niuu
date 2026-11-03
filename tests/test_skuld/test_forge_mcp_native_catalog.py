"""Native Claude must retain every tool returned by Forge MCP discovery.

Opt in with FORGE_LIVE_CLI=1 and -m live_cli. This uses the installed Claude CLI
only for initialize/get_mcp_status, never a query or provider call. Its child has
an isolated configuration directory, no inherited credentials and a loopback
provider URL. No existing Forge session or service is contacted.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from niuu.forge_mcp.models import ForgeMcpNotifyMode
from niuu.forge_mcp.tools import ForgeMcpToolbox
from tests.test_niuu.test_forge_mcp_tools import LIMITS, FakeClient, FakeHost

# Use the real stdio implementation; the fixture replaces only its broker HTTP
# client with the actual catalog being tested. A tool call is forbidden.
_CATALOG_SERVER = """
import json, sys
from skuld.forge_mcp.stdio import StdioServer
class CatalogClient:
    def list_tools(self):
        with open(sys.argv[1]) as handle:
            return json.load(handle)
    def call_tool(self, *args):
        raise AssertionError('catalog probe cannot invoke tools')
StdioServer(CatalogClient(), stdin=sys.stdin.buffer, stdout=sys.stdout.buffer,
            max_concurrency=1).serve()
"""

_NATIVE_PROBE = """
import asyncio, json, pathlib, sys
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
async def main():
    root = pathlib.Path(sys.argv[2])
    options = ClaudeAgentOptions(
        cli_path=sys.argv[1], cwd=str(root), strict_mcp_config=True,
        setting_sources=[],
        mcp_servers={name: {
            'type': 'stdio', 'command': sys.executable,
            'args': ['-P', str(root / 'catalog.py'), str(root / (name + '.json'))],
        } for name in ('forge', 'forge_feed')},
    )
    async with asyncio.timeout(30):
        async with ClaudeSDKClient(options) as client:
            while True:
                result = await client.get_mcp_status()
                servers = result.get('mcpServers', [])
                if len(servers) == 2 and all(s.get('status') != 'pending' for s in servers):
                    print(json.dumps({s['name']: {
                        'status': s['status'],
                        'tools': [t['name'] for t in s.get('tools', [])],
                    } for s in servers}))
                    return
                await asyncio.sleep(0.1)
asyncio.run(main())
"""


@pytest.mark.live_cli
def test_native_claude_keeps_notify_and_the_complete_forge_catalog(tmp_path):
    if os.environ.get("FORGE_LIVE_CLI") != "1":
        pytest.skip("set FORGE_LIVE_CLI=1 for the native catalog-only check")
    cli = shutil.which("claude")
    if cli is None:
        pytest.skip("Claude CLI is not installed")
    config = tmp_path / "claude-config"
    config.mkdir()
    (tmp_path / "catalog.py").write_text(_CATALOG_SERVER)
    expected = {}
    for name, mode in (
        ("forge", ForgeMcpNotifyMode.TRANSCRIPT),
        ("forge_feed", ForgeMcpNotifyMode.FEED),
    ):
        catalog = ForgeMcpToolbox(
            client=FakeClient(),
            host=FakeHost(),
            grants=(),
            limits=LIMITS,
            notify_mode=mode,
        ).list_tools()
        expected[name] = {tool["name"] for tool in catalog}
        (tmp_path / f"{name}.json").write_text(json.dumps(catalog))
    result = subprocess.run(
        [sys.executable, "-c", _NATIVE_PROBE, cli, str(tmp_path)],
        env={
            "PATH": os.defpath,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
            "CLAUDE_CONFIG_DIR": str(config),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:1",
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
        },
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    for name, tools in expected.items():
        assert actual[name]["status"] == "connected"
        # R34: the server was connected and raw discovery included notify, but
        # Claude silently removed only that tool because of its root allOf.
        assert "notify" in actual[name]["tools"]
        assert set(actual[name]["tools"]) == tools
