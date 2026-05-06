# Ravn Mesh E2E Testing

How to spin up and test a 3-node Ravn mesh locally.

## Prerequisites

- Python 3.12+ with the repo's virtualenv active
- `~/.ravn/realm.key` exists (shared HMAC key for handshakes)
- Persona files at `~/.ravn/personas/`: `coder.yaml`, `reviewer.yaml`, `security.yaml`

## Quick Start

```bash
# 1. Start the 3-node mesh (coder, reviewer, security)
scripts/ravn-mesh.sh start

# 2. Publish a test event to trigger the reviewer
RAVN_CONFIG=/tmp/ravn-mesh/ravn-mesh-1.yaml \
  PYTHONPATH=src python -m ravn publish code.changed \
    --payload '{"files_changed": 1, "summary": "initial test code"}'

# 3. Watch the event chain play out
scripts/ravn-mesh.sh logs

# 4. Check node health
scripts/ravn-mesh.sh status

# 5. Stop everything
scripts/ravn-mesh.sh stop
```

## Architecture

### Node Layout

| Node | Persona | Consumes | Produces | Ports (pub/rep/handshake) |
|------|---------|----------|----------|--------------------------|
| 1 | coder | `review.completed` | `code.changed` | 7480 / 7481 / 7490 |
| 2 | reviewer | `code.changed` | `review.completed` | 7482 / 7483 / 7491 |
| 3 | security | `review.completed` | `security.completed` | 7484 / 7485 / 7492 |

### Event Chain

```
test-publisher ──[code.changed]──► reviewer
                                      │
                              [review.completed]
                                      │
                      ┌───────────────┴───────────────┐
                      ▼                               ▼
                   coder                          security
                      │                               │
              [code.changed]                [security.completed]
                      │
                      ▼
                  reviewer (re-review)
```

The reviewer triggers both the coder and security persona in parallel.
The coder's fix triggers a re-review, forming the feedback loop.

## Key Configuration

All generated configs live at `/tmp/ravn-mesh/ravn-mesh-{1,2,3}.yaml`.

Critical settings:
- `permission.workspace_root: /tmp` — allows file tools to access `/tmp/hello.py` (the demo target)
- `mesh.adapter: nng` — IPC-based pub/sub transport
- `discovery.adapter: mdns` — local mDNS peer discovery with handshake ports
- `memory.backend: sqlite` — per-node SQLite at `/tmp/ravn-mesh/ravn-mesh-{n}.db`

## Personas

Persona files live at `~/.ravn/personas/` and define each node's behavior:

### coder.yaml
- Reads `/tmp/hello.py`, applies fixes from review comments, writes it back
- `allowed_tools: [file, terminal]`
- `iteration_budget: 50`

### reviewer.yaml
- Reads `/tmp/hello.py`, checks for SQL injection, command injection, hardcoded secrets, bare excepts
- Produces `verdict: pass | fail | needs_changes`
- `allowed_tools: [file]`
- `iteration_budget: 15`

### security.yaml
- Reads `/tmp/hello.py`, scans for security vulnerabilities
- Produces `verdict: secure | vulnerable | needs_review` with `findings_count`
- `allowed_tools: [file, git]`
- `iteration_budget: 20`

## Log Capture

Each test run should be captured to `logs/mesh-demo-YYYYMMDD-HHMMSS/` containing:

| File | Description |
|------|-------------|
| `ravn-mesh-1.log` | Node 1 (coder) output |
| `ravn-mesh-2.log` | Node 2 (reviewer) output |
| `ravn-mesh-3.log` | Node 3 (security) output |
| `ravn-mesh-{1,2,3}.yaml` | Generated config snapshots |
| `test-publisher.log` | Test publisher output |
| `sleipnir-registry.json` | Peer registry snapshot |
| `README.md` | Timeline and summary of results |

To capture logs from a running mesh:

```bash
# Copy generated configs
cp /tmp/ravn-mesh/ravn-mesh-*.yaml logs/mesh-demo-$(date +%Y%m%d-%H%M%S)/

# Logs are already written to /tmp/ravn-mesh/ravn-mesh-{1,2,3}.log
cp /tmp/ravn-mesh/ravn-mesh-*.log logs/mesh-demo-$(date +%Y%m%d-%H%M%S)/
```

## What to Verify

1. **Peer discovery**: All 3 nodes find each other via mDNS (check logs for `discovered N publisher(s)`)
2. **Event routing**: `code.changed` reaches reviewer; `review.completed` reaches both coder and security
3. **Parallel consumption**: Coder and security both receive `review.completed` independently
4. **Feedback loop**: Coder publishes `code.changed` after fixing, reviewer re-reviews
5. **Loop termination**: Reviewer eventually returns `verdict=pass`, ending the cycle
6. **Outcome blocks**: Each node produces a valid outcome matching its persona schema

## Known Issues

### Mesh Discovery Race Condition

Early-started nodes may not discover late-started nodes. Fixed by adding periodic
re-discovery in `NngSubscriber._rediscover_loop()` (in `sleipnir/adapters/nng_transport.py`).
The script staggers node starts by 2 seconds to mitigate this.

### Workspace Permissions

The coder persona needs `permission.workspace_root: /tmp` to read/write `/tmp/hello.py`.
Without this, the coder will claim to fix bugs but won't actually modify the file.

## Script Reference

```
scripts/ravn-mesh.sh start   — start 3 nodes in background
scripts/ravn-mesh.sh stop    — stop all 3 nodes
scripts/ravn-mesh.sh status  — show running nodes
scripts/ravn-mesh.sh logs    — tail all logs
scripts/ravn-mesh.sh peers   — list verified flock members on node 1
```
