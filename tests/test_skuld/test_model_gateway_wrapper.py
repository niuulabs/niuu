"""Verify gateway wrapper routing, credential isolation and JWT refresh."""

import socket
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = "https://volundr.example.invalid/api/v1/bifrost"


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class GatewayTest(unittest.TestCase):
    def setUp(self):
        manifest = ROOT / "containers/skuld/gateway-wrapper"
        source = manifest.read_text()
        stub = types.ModuleType("niuu.adapters.outbound.http_auth")
        stub.WorkloadIdentityBearerTokenAuthAdapter = lambda **kwargs: None
        self.module = types.ModuleType("gateway")
        with patch.dict(sys.modules, {"niuu.adapters.outbound.http_auth": stub}):
            exec(compile(source, str(manifest), "exec"), self.module.__dict__)
        self.listen = f"http://127.0.0.1:{_free_port()}"

    def _gateway_env(self, **extra):
        return {
            "SKULD__MODEL_GATEWAY__URL": self.listen,
            "NIUU_GATEWAY_UPSTREAM": UPSTREAM,
            **extra,
        }

    def test_passes_through_when_gateway_is_not_configured(self):
        """No model is named in the wrapper — absent config means no gateway."""
        with (
            patch.object(sys, "argv", ["claude", "--model", "claude-sonnet-5"]),
            patch.dict(self.module.os.environ, {}, clear=True),
            patch.object(self.module.os, "execv", side_effect=SystemExit) as execute,
        ):
            with self.assertRaises(SystemExit):
                self.module.main()
        execute.assert_called_once_with(
            "/usr/local/bin/claude", ["/usr/local/bin/claude", "--model", "claude-sonnet-5"]
        )

    def test_passes_through_when_upstream_is_missing(self):
        with (
            patch.object(sys, "argv", ["claude"]),
            patch.dict(
                self.module.os.environ, {"SKULD__MODEL_GATEWAY__URL": self.listen}, clear=True
            ),
            patch.object(self.module.os, "execv", side_effect=SystemExit),
        ):
            with self.assertRaises(SystemExit):
                self.module.main()

    def test_passes_through_when_listener_is_not_loopback(self):
        """A non-loopback URL is Bifrost itself; serving it here would be wrong."""
        env = self._gateway_env(SKULD__MODEL_GATEWAY__URL="https://bifrost.example.invalid")
        with (
            patch.object(sys, "argv", ["codex"]),
            patch.dict(self.module.os.environ, env, clear=True),
            patch.object(self.module.os, "execv", side_effect=SystemExit),
        ):
            with self.assertRaises(SystemExit):
                self.module.main()

    def _run_gateway(self, argv0, cli, env_extra=None):
        requests = []
        auth = types.SimpleNamespace(
            headers=lambda: {"Authorization": "Bearer exchanged-test-token"}
        )

        def upstream(request):
            requests.append(request)
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=b"data: first\n\ndata: second\n\n",
            )

        upstream_client = httpx.Client(transport=httpx.MockTransport(upstream))
        with (
            patch.object(sys, "argv", [argv0]),
            patch.dict(self.module.os.environ, self._gateway_env(**(env_extra or {})), clear=True),
            patch.object(self.module, "WorkloadIdentityBearerTokenAuthAdapter", return_value=auth),
            patch.object(auth, "headers", wraps=auth.headers) as headers,
            patch.object(self.module.httpx, "Client", return_value=upstream_client),
            patch.object(self.module, "_session_model", return_value="deepseek-v4-flash-0731"),
            patch.object(self.module.subprocess, "call", side_effect=cli),
        ):
            result = self.module.main()
        return result, requests, headers

    def test_claude_is_redirected_with_a_fresh_token_per_request(self):
        real_client = httpx.Client

        def cli(args, env):
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertEqual(env["ANTHROPIC_BASE_URL"], self.listen)
            self.assertEqual(env["ANTHROPIC_MODEL"], "deepseek-v4-flash-0731")
            with real_client() as client:
                for _ in range(2):
                    result = client.post(
                        env["ANTHROPIC_BASE_URL"] + "/v1/messages",
                        headers={
                            "x-api-key": "must-not-forward",
                            "Authorization": "Bearer must-not-forward",
                        },
                        json={"model": "deepseek-v4-flash-0731"},
                    )
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.content, b"data: first\n\ndata: second\n\n")
            return 0

        result, requests, headers = self._run_gateway(
            "claude",
            cli,
            env_extra={"CLAUDE_CODE_OAUTH_TOKEN": "test", "ANTHROPIC_API_KEY": "test"},
        )

        self.assertEqual(result, 0)
        self.assertEqual(headers.call_count, 2)
        self.assertEqual(len(requests), 2)
        for request in requests:
            self.assertEqual(str(request.url), UPSTREAM + "/v1/messages")
            self.assertEqual(request.headers["Authorization"], "Bearer exchanged-test-token")
            self.assertNotIn("x-api-key", request.headers)

    def test_codex_gets_the_listener_but_no_anthropic_redirect(self):
        """Codex is pointed at the gateway by skuld's transport, not by us."""
        real_client = httpx.Client
        seen = {}

        def cli(args, env):
            seen["anthropic_base_url"] = env.get("ANTHROPIC_BASE_URL")
            with real_client() as client:
                result = client.post(self.listen + "/v1/responses", json={})
                self.assertEqual(result.status_code, 200)
            return 0

        result, requests, _ = self._run_gateway("codex", cli)

        self.assertEqual(result, 0)
        self.assertIsNone(seen["anthropic_base_url"])
        self.assertEqual(str(requests[0].url), UPSTREAM + "/v1/responses")

    def test_non_v1_paths_are_not_proxied(self):
        real_client = httpx.Client

        def cli(args, env):
            with real_client() as client:
                self.assertEqual(client.get(self.listen + "/healthz").status_code, 404)
            return 0

        _, requests, _ = self._run_gateway("claude", cli)
        self.assertEqual(requests, [])


if __name__ == "__main__":
    unittest.main()
