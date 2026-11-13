# The `memory` MCP server in a session

Every session can get one built-in MCP server, `memory`, over all the memory layers
the session may see. It sits beside the built-in `forge` server and is added to the
same engines: Claude Code (every transport) and Codex. The design is
[`docs/design/memory/README.md`](../design/memory/README.md) sections 5 and 6.

## How it works

1. The engine starts the stdio process, `python -m skuld.forge_mcp --server memory`.
   It is the same process as the `forge` server, told which server to be. It holds no
   credential, only the path of the broker's loopback secret file.
2. At `initialize` the stdio process asks the session's broker
   (`GET /api/memory-mcp/tools`). The broker asks the memory gateway
   (`GET {memory.gateway_url}/memory/tools`) and returns its `instructions` and
   `tools`. The instructions the agent reads are the gateway's.
3. Each tool call goes to `POST /api/memory-mcp/tools/{name}` on the broker, which
   calls `POST {memory.gateway_url}/memory/tools/{name}`.
4. The broker authenticates to the gateway with the session's Forge credential
   (`forge_mcp.token`, scope `forge:memory`). That credential stays in the broker and is
   removed from every agent process's environment. The gateway enforces the layer,
   write and trust rules; Skuld only carries the call, and the server's tools are
   approved by default (`default_tools_approval_mode: approve`).

## Context at launch

When memory is enabled, the broker also asks the gateway for the session's context pack
before it creates the harness (`GET {memory.gateway_url}/memory/context-pack`, with the
same Forge credential). The pack's `text` (the entities in the project brief and the
objective, their open threads, the facts that hold now, what changed since the owner's
last session) is added to the harness instructions under the heading
`## Memory context at launch`: Claude Code's `--append-system-prompt` and Codex's
developer instructions (app-server and `exec`), and any other engine that takes the
session's capability text.

- The pack is fetched **once per broker start**. A resumed harness gets the same text, not
  a fresh fetch.
- The pack is not sized from the model's context window: it holds everything the gateway
  selects. The answer is `{"text": "..."}`; an answer without a `text` string stops the start.
- If the gateway cannot be reached, refuses the credential, times out
  (`memory.context_pack_timeout_s`) or answers a malformed pack, the broker does not
  start. `/ready` reports `startup_state: failed` with an error naming
  `<gateway_url>/memory/context-pack` and the reason, and Forge is told the start failed.
  No empty pack stands in for a missing one. Set `memory.enabled` to `false` to run a
  session without memory.
- The credential is used only for this call. It is not in the harness arguments or
  environment; the pack text itself is.

## Configuration

Forge sets these for every session when its own `memory.gateway_url` is configured.

| Setting | Env | Default | Meaning |
|---|---|---|---|
| `memory.enabled` | `SKULD__MEMORY__ENABLED` | `false` | Inject the server |
| `memory.gateway_url` | `SKULD__MEMORY__GATEWAY_URL` | empty | The gateway's API base, e.g. `http://127.0.0.1:8080/api/v1/mimir` |
| `memory.server_name` | `SKULD__MEMORY__SERVER_NAME` | `memory` | The MCP server name |
| `memory.gateway_request_timeout_s` | | `120` | One broker to gateway call |
| `memory.broker_request_timeout_s` | | `150` | One stdio to broker call |
| `memory.tool_timeout_s` | | `180` | The engine's own tool-call timeout (Codex) |
| `memory.context_pack_timeout_s` | | `30` | The one broker to gateway call that fetches the context pack at startup |
| `memory.startup_timeout_s` | | `20` | The engine's server startup timeout (Codex) |
| `memory.stdio_max_concurrency` | | `4` | Tool calls served in parallel |

The three timeouts must nest (gateway, then broker, then engine), or startup fails
saying which to change.

## Failures are loud

- `memory.enabled` with an empty `memory.gateway_url`, or without the session's Forge
  credential (`SKULD__FORGE_MCP__TOKEN` or `forge_mcp.token_file`), stops the broker at
  startup and names the setting.
- If the gateway cannot be reached when the agent connects, `initialize` fails with the
  gateway's URL; the agent never gets a memory server with no instructions or no tools.
- A tool call to an unreachable gateway, a gateway error status, or a malformed answer
  comes back as an error result naming the gateway URL and what happened.
- HTTP 401 or 403 from the gateway reads "refused this session's memory credential": the
  credential lacks `forge:memory`, or its session or launch ended.
- A rule refusal (a write the layer rules deny) is the gateway's own error result,
  passed through unchanged.
