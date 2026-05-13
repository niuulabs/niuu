# Security & Permissions

Ravn's permission system controls what the agent can do — which tools it can
call, which commands it can execute, and which files it can access.

## Permission Modes

| Mode | Description |
|------|-------------|
| `read_only` | No mutations. Bash limited to read-only commands, no network. |
| `workspace_write` (default) | Writes allowed within `workspace_root` only. |
| `full_access` | Unrestricted access. Explicit opt-in required. |
| `prompt` | Interactive approval for each action. |

Set the mode in config:

```yaml
permission:
  mode: workspace_write
  workspace_root: "/home/user/projects/myapp"
```

## Permission Rules

Rules are evaluated in order before the default mode. First match wins.

```yaml
permission:
  mode: workspace_write
  rules:
    - pattern: "bash:execute"
      action: ask            # Always prompt for bash
    - pattern: "git:write"
      action: allow          # Auto-allow git writes
    - pattern: "web:*"
      action: deny           # Block all web tools
  allow:
    - read_file              # Always allow these tools
    - glob_search
  deny:
    - terminal_docker        # Always block these tools
```

### Pattern Matching

| Pattern | Matches |
|---------|---------|
| `bash:execute` | Exact permission name. |
| `git:*` | All git permissions. |
| `*:read` | All read permissions. |
| `web:*` | All web permissions. |

## Bash Command Validation

The bash tool has a multi-stage security pipeline:

### Stage 1: Command Intent Classification

Every bash command is classified by intent:

| Intent | Description | Examples |
|--------|-------------|---------|
| `READ_ONLY` | Safe information retrieval | `ls`, `cat`, `grep`, `find` |
| `WRITE` | Data mutation (safe) | `echo > file`, `mkdir` |
| `DESTRUCTIVE` | Dangerous mutations | `rm`, `rmdir`, `truncate` |
| `NETWORK` | Network calls | `curl`, `wget`, `ssh` |
| `PROCESS_MANAGEMENT` | Process control | `kill`, `pkill` |
| `PACKAGE_MANAGEMENT` | Package operations | `apt`, `pip`, `npm` |
| `SYSTEM_ADMIN` | System administration | `usermod`, `mount` |

### Stage 2: Path Validation

- **Symlink resolution** — follows symlinks to check real paths
- **Path traversal detection** — blocks `../` escapes from workspace
- **System path protection** — blocks writes to `/bin`, `/sys`, `/root`, etc.

### Stage 3: Always-Blocked Commands

Certain commands are always blocked regardless of mode:

- `rm -rf` (directory deletion)
- `dd` (raw disk writes)
- `sudo` (privilege escalation)
- `chmod 777` (dangerous permissions)

### Stage 4: Permission Mode Check

After classification and validation, the command is checked against the
active permission mode.

## Approval Memory

When `permission.mode` is `prompt`, the agent asks the user before executing
sensitive commands. Approved commands are remembered per-project:

```bash
# View approved patterns
ravn approvals list

# Revoke an approval
ravn approvals revoke "npm test"
```

Approval data is stored in `.ravn/approvals.json` within the project.

## Personal Access Tokens (PATs)

For service-to-service authentication (e.g., Tyr calling Volundr), Ravn
supports Personal Access Tokens. PATs are long-lived JWTs signed with the
same symmetric key that Envoy validates.

PATs exist in the shared `niuu` module and integrate with existing
infrastructure without requiring identity provider changes.

## Configuration

```yaml
permission:
  mode: workspace_write
  workspace_root: "/home/user/projects"
  allow: []
  deny: []
  ask: []
  rules: []
```

See the [Configuration Reference](../configuration/reference.md#permission) for all fields.
