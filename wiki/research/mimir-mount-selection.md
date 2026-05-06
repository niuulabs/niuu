---
type: research
confidence: high
produced_by_thread: true
related_entities: [best-practices-agent-mimir-pages]
source_ids: [src_niu775_composite_adapter, src_niu775_domain_models, src_niu775_mimir_tools, src_niu775_registry, src_niu775_mcp_tools_spec, src_niu775_mcp_architecture]
---

# Mimir Mount Selection for Agents

> **TL;DR** — Expose one composite Mimir MCP server, let reads fan out across all mounts, default writes to `local`, and use an explicit `mimir="shared"` override only when promoting vetted output. Do not make agents choose by MCP server name.

## Recommended Routing Model

| Mount | Use it for | Agent write behavior |
|---|---|---|
| `local` | Scratch notes, drafts, session-only work, uncertain synthesis | Default target |
| `shared` | Reusable pages other agents should trust | Write only via explicit promotion |
| `domain-*` | Read-only corpora such as vendor docs, code snapshots, policy archives | Never agent-write by default |

Recommended behavior:

1. Reads stay implicit. `CompositeMimirAdapter` already searches mounts in `read_priority` order and de-duplicates by path, so the agent should call `mimir_search` and `mimir_read` without choosing a mount first.
2. Writes default to `local`. `WriteRouting` falls back to `["local"]` when no rule matches, which is the safest default for research and scratch work.
3. Promotion to `shared` is explicit. `mimir_write(..., mimir="shared")` bypasses prefix routing and makes the promotion step deliberate.
4. `domain-*` mounts stay read-only and operator-managed. They are useful context, not a destination for routine agent output.

Recommended Niuu routing:

```python
WriteRouting(
    rules=[
        ("self/", ["local"]),
        ("technical/", ["local", "shared"]),
        ("household/", ["shared"]),
    ],
    default=["local"],
)
```

For `research/`, keep the default `local` write path and promote only the final distilled page to `shared`. This is a better fit than the older docs example that routes `research/` to `["shared", "domain"]`, because it avoids auto-writing into read-mostly corpora and keeps promotion intentional.

## Naming and Description Scheme

Mount names should encode role first, corpus second.

| Name | Description shown to the model |
|---|---|
| `local` | Session scratch Mimir. Use for drafts, notes, and uncertain work. |
| `shared` | Team shared Mimir. Use only for vetted pages worth reusing. |
| `domain-product-docs` | Read-only product documentation corpus. Search and read; do not write. |
| `domain-codebase-snapshots` | Read-only codebase reference corpus. Use for background context only. |

Guidelines:

- Prefer short role-first names: `local`, `shared`, `domain-*`.
- Put the write policy in the description, not just the name.
- Make `domain-*` descriptions explicitly say `read-only`.
- Keep the `mimir_write` tool description aligned with the same language so the model sees one consistent routing story.

## Implicit vs Explicit vs Server-Name-Based

| Choice | Recommendation | Why |
|---|---|---|
| Implicit path-based routing | Yes, as the default | Lowest cognitive load; safe default writes |
| Explicit `mimir` parameter | Yes, for promotion and exceptions | Makes cross-mount intent deliberate and auditable |
| Separate-server choice by server name | No | MCP clients aggregate tools from multiple servers into one registry, and the MCP spec says server names are not guaranteed to be unique |

Best contract for agents:

- `mimir_search` / `mimir_read`: no mount parameter
- `mimir_write`: optional `mimir` parameter
- registry/mount metadata: clear `name`, `role`, `desc`, and read priority

That gives the model one normal path and one explicit escape hatch, instead of forcing it to reason about infrastructure topology on every write.

## One Composite Server vs Multiple MCP Servers

| Option | Benefits | Costs |
|---|---|---|
| One composite Mimir MCP server | One tool set, transparent read fan-out, centralized routing, fewer tool slots | Requires server-side routing logic and mount metadata discipline |
| Separate MCP server per mount | Strong isolation, per-server auth boundaries | Higher agent confusion, more tools, duplicated `search/read/write`, weaker defaults |

Use multiple MCP servers only when mounts truly cannot share one trust boundary or one routing layer. Otherwise, a composite server is better for both agent accuracy and operator control.

## Risks and Edge Cases

- Unknown mount names are only logged today. A bad routing config can silently skip writes.
- A stale `local` page shadows a fresher `shared` page because lower `read_priority` wins.
- Dual-write routing can leave `local` and `shared` inconsistent after partial failure.
- An explicit `mimir="domain-*"` override can bypass policy unless the domain adapter is actually read-only.
- If mount descriptions drift away from routing behavior, the model will make the wrong choice even when the code is correct.

## Recommendation for Niuu

1. Keep one composite Mimir MCP server per persona or runtime, not one server per mount.
2. Standardize on `local`, `shared`, and optional `domain-*` mounts.
3. Make `local` the default write target for `research/` and require explicit promotion to `shared`.
4. Keep `domain-*` read-only at the adapter level, not just by convention.
5. Treat mount descriptions as part of the tool contract and keep them synchronized with routing rules.

## Sources

- `src/ravn/domain/mimir.py` — `MimirMount` and `WriteRouting` define role, read priority, and explicit routing override behavior. (niuulabs/volundr, retrieved 2026-05-05)
- `src/ravn/adapters/mimir/composite.py` — `CompositeMimirAdapter` fans out reads and routes writes by explicit `mimir`, then prefix rules, then default fallback. (niuulabs/volundr, retrieved 2026-05-05)
- `src/ravn/adapters/tools/mimir_tools.py` — `mimir_write` exposes the optional `mimir` parameter and already describes promotion to `shared`. (niuulabs/volundr, retrieved 2026-05-05)
- `src/mimir/registry.py` — registry entries carry `name`, `role`, `desc`, and read-priority metadata that should guide mount selection. (niuulabs/volundr, retrieved 2026-05-05)
- MCP tools specification — tools are selected by name, description, and input schema; server names are not a safe disambiguation mechanism. (https://modelcontextprotocol.io/specification/draft/server/tools, retrieved 2026-05-05)
- MCP architecture overview — MCP hosts combine tools from all connected servers into a unified registry visible to the model. (https://modelcontextprotocol.io/docs/learn/architecture, retrieved 2026-05-05)

<!-- sources: src_niu775_composite_adapter, src_niu775_domain_models, src_niu775_mimir_tools, src_niu775_registry, src_niu775_mcp_tools_spec, src_niu775_mcp_architecture -->
