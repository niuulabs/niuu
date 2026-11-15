"""The executables an OpenShell sandbox lets reach an agent harness's own endpoints.

OpenShell admits a connection by the binary the process actually runs (its binary
identity), not by the name it was started under. Claude Code's npm package ships a
native executable (``@anthropic-ai/claude-code/bin/claude.exe``) behind the
``/usr/local/bin/claude`` symlink, so listing the symlink alone admits nothing: every
call to ``api.anthropic.com`` was refused as ``transparent_tcp_policy_denied`` and the
CLI reported ECONNREFUSED. The paths below cover the layouts our images build (the Skuld
and OpenShell images install the CLIs under ``/opt/skuld-tools``; the OpenShell image
moves Codex under ``/usr/lib/node_modules``), the CLI the Claude Agent SDK bundles in
its wheel, and node for the older script entry points.
"""

#: Claude Code, whichever entry point a transport starts.
CLAUDE_CODE_BINARIES = (
    "/usr/local/bin/claude",
    "/opt/skuld-tools/node_modules/@anthropic-ai/**",
    "/usr/lib/node_modules/@anthropic-ai/**",
    "/opt/venv/lib/python*/site-packages/claude_agent_sdk/_bundled/**",
    "/opt/niuu/lib/python*/site-packages/claude_agent_sdk/_bundled/**",
    "/usr/bin/node",
    "/usr/local/bin/node",
)

#: Codex: a node shim in front of a native binary in its platform package.
CODEX_BINARIES = (
    "/usr/local/bin/codex",
    "/opt/skuld-tools/node_modules/@openai/**",
    "/usr/lib/node_modules/@openai/**",
    "/usr/bin/node",
    "/usr/local/bin/node",
)
