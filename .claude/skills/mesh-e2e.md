---
name: mesh-e2e
description: Run end-to-end validation of the Ravn mesh with browser visualization via Skuld
---

# Ravn Mesh E2E Test

Validates the full mesh event cascade across three personas with live browser
visualization through the Skuld WebSocket broker.

## Mesh Nodes

| Node | Persona | Display Name | Consumes | Produces |
|------|---------|-------------|----------|----------|
| 1 | coder | Kvasir | review.completed | code.changed |
| 2 | reviewer | Bragi | code.changed | review.completed |
| 3 | security | Heimdall | review.completed | security.completed |

## Cascade Flow

```
user message (via Skuld browser) ──→ all nodes receive
                                        ↓
coder (Kvasir) ── code.changed ──→ reviewer (Bragi)
                                        ↓
                                  review.completed
                                   ↓          ↓
                             coder (Kvasir)  security (Heimdall)
```

## Quick Start

### 1. Start Skuld (the broker)

Skuld must be running for browser delivery. Start it in a separate terminal:

```bash
make skuld
# or: uv run python -m skuld
```

Default: `ws://localhost:8081/ws/ravn`

### 2. Start the Mesh

```bash
scripts/ravn-mesh.sh start
```

This spawns 3 ravn daemon nodes with:
- nng mesh transport (IPC sockets at `/tmp/ravn-mesh/`)
- Static discovery via `/tmp/ravn-mesh/cluster.yaml`
- Skuld channel for browser delivery (display names: Kvasir, Bragi, Heimdall)
- Config files at `/tmp/ravn-mesh/ravn-mesh-{1,2,3}.yaml`

### 3. Open Browser

Navigate to the Volundr web UI and open a mesh session. You should see all
three agents in the sidebar: **Kvasir (coder)**, **Bragi (reviewer)**,
**Heimdall (security)**.

### 4. Test File

The test uses `/tmp/hello.py` — a deliberately buggy user management API with
SQL injection, plaintext passwords, command injection, hardcoded secrets, etc.
Reset it before each run:

```python
# /tmp/hello.py should contain functions with these bugs:
# - f-string SQL queries (SQL injection)
# - plaintext password storage and comparison
# - os.popen with unsanitized input (command injection)
# - hashlib.md5 for tokens (weak hashing)
# - hardcoded SECRET_KEY
# - passwords leaked in list_users() and export_users()
# - missing connection.close() in delete_user()
# - no auth check in promote_user()
# - no path traversal protection in export_users()
```

### 5. Send a Message

Type a message in the chat like:

> Review /tmp/hello.py for security issues and fix them

All three agents work on it:
- **Kvasir (coder)** applies fixes, publishes `code.changed`
- **Bragi (reviewer)** reviews the changes, publishes `review.completed`
- **Heimdall (security)** does a security audit, publishes `security.completed`

### 6. What to Verify in Browser

- Agents appear in sidebar with display names and status indicators
- Status transitions: idle → thinking → tool_executing → idle
- Internal events (thinking, tool calls) visible via **Internal** toggle in toolbar
- Internal events use the same tool grouping UI as regular messages
- Mesh cascade events visible in the right panel (outcomes, delegations)
- Mesh events persist across page refresh
- Click a sidebar peer to filter chat to that participant
- **Clear chat** button (trash icon) resets everything
- @-mentioning a specific ravn via the chat input directs the message to that agent

### 7. Stop Mesh

```bash
scripts/ravn-mesh.sh stop
```

The stop command now force-kills strays, so stale processes from crashed runs
are cleaned up automatically.

## Monitor Logs

```bash
scripts/ravn-mesh.sh logs
# Or watch specific node:
tail -f /tmp/ravn-mesh/ravn-mesh-2.log | grep -E "(mesh:|drive_loop:|SkuldChannel)"
```

## Troubleshooting

### Agents Don't Appear in Browser

1. **Skuld not running**: Start it with `make skuld`
2. **Stale processes**: `scripts/ravn-mesh.sh stop` now kills strays automatically
3. **Config missing display_name**: Check `/tmp/ravn-mesh/ravn-mesh-*.yaml` has `display_name` under `skuld:`

### Events Not Received

1. **Wrong event format**: Must use `ravn_type: "outcome"` — the handler filters on this
2. **Publisher not discovered**: Wait 15s — nng discovery polls every 5-10s
3. **Stale IPC sockets**: `scripts/ravn-mesh.sh stop && rm -rf /tmp/ravn-mesh/*.ipc && scripts/ravn-mesh.sh start`

### Agents Stuck in "thinking"

The `response` event resets status to idle. If an agent errors out without
sending a response, it stays in thinking. Check its log for exceptions.
`outcome` and `help_needed` events do NOT reset status (they happen mid-turn).

### Directed Messages (@-mentions) Not Working

The browser sends `targetPeerId` (camelCase) to the broker, which routes via
`RoomBridge.route_directed_message()` to the ravn's WebSocket. The ravn's
`SkuldChannel` receive loop picks it up and enqueues it as a high-priority
`AgentTask` in the drive loop.

## Files

- `scripts/ravn-mesh.sh` — mesh lifecycle (start/stop/status/logs/peers)
- `/tmp/ravn-mesh/cluster.yaml` — static peer definitions with display names
- `/tmp/ravn-mesh/ravn-mesh-{1,2,3}.yaml` — per-node config (generated)
- `/tmp/ravn-mesh/ravn-mesh-{1,2,3}.log` — per-node logs
- `src/skuld/room_bridge.py` — Ravn→browser event translation
- `src/skuld/broker.py` — WebSocket broker, ravn registration, directed messages
- `src/ravn/adapters/channels/skuld_channel.py` — ravn→Skuld WebSocket channel
- `src/ravn/drive_loop.py` — task execution, Skuld eager connect
- `web/src/modules/shared/hooks/useSkuldChat.ts` — browser-side event handling
