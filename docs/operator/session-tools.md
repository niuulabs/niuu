# Session tools

A session tool is an MCP server a session asks for by name. The operator keeps the
catalogue (`mcp_servers` in the Forge config, `mcpServers` in the volundr chart); any
session, on any harness that takes MCP servers (Claude Code, Codex, Grok) and any
placement, can ask for any of its entries. Nothing is built into the session images.

## The catalogue

An entry runs a `command`, reaches a `url`, or runs a pinned `program`:

```yaml
mcpServers:
  - name: solidcraft
    description: "Parametric CAD."
    args: ["mcp", "--headless"]
    default_tools_approval_mode: approve   # Codex asks before every MCP call otherwise
    program:
      version: "0.46.0"
      platforms:
        linux-x86_64:
          url: https://github.com/tangency-labs/solidcraft/releases/download/v0.46.0/solidcraft-0.46.0-linux-x86_64.tar.gz
          sha256: 27b3b8334fbf99ab376d0bea176afaf55ef83b7aff822eb731eddb8d632b9112
          command: solidcraft-0.46.0-linux-x86_64/bin/solidcraft-cli
```

- `platforms` keys are `linux-x86_64`, `linux-arm64`, `darwin-x86_64` and `darwin-arm64`.
- Archives are `tar`, `tar.gz`, `tar.xz`, `tar.bz2` or `zip`, over `https`. A `.dmg` is
  not: repackage its program as a tar and pin that.
- `command` is the executable's path inside the archive.
- To upgrade, change the version, URLs and digests. Sessions that start afterwards get it.

`GET /api/v1/credentials/mcp-servers` lists the catalogue, with each program's version.

## Asking for a tool

- `POST /api/v1/forge/sessions` with `"mcp_servers": ["solidcraft"]`.
- The Forge MCP's `create_session` with `mcp_servers`, so a coordinator gives its workers
  the tools it has itself.
- A launch spec with `mcpServers: [{name: solidcraft}]`, for a session that always has it.

A name that is not in the catalogue is refused (422 on create), with the names there are.
A restart gives the session the same tools.

## What happens at start

Before the engine starts, Skuld picks this platform's archive, downloads it, checks its
SHA-256, unpacks it and runs its `command` as the MCP server. That is the same in a pod, on
a VM, on a registered Linux host or Mac, and in mini mode. Programs are kept in
`.cache/niuu/programs/<sha256>` in the session's home (`programs.cache_dir`), so the next
session with that home starts without a download.

The session does not start, and says why, when:

- the platform it runs on has no archive;
- the archive cannot be fetched, or its digest is not the pinned one;
- the archive has no such `command`;
- the harness takes no MCP servers (OpenCode, Muse, Pi, dsh, Claude remote control).

A session whose network policy limits egress (OpenShell) must be allowed to reach the
archive's host and what it redirects to; GitHub releases redirect to
`release-assets.githubusercontent.com`.
