---
type: research
confidence: high
produced_by_thread: true
related_entities: [best-practices-agent-mimir-pages]
source_ids: [src_niu775_composite_adapter, src_niu775_domain_models, src_niu775_mimir_tools, src_niu775_mcp_server, src_niu775_registry]
---

# Mimir Mount Selection for Agents

> **TL;DR** — Use a single composite Mimir MCP server with three named mount roles (`local`, `shared`, `domain`). Reads fan out automatically; writes default to `local`. Agents promote content to `shared` via the explicit `mimir="shared"` override. Name mounts and their tool descriptions to make the right choice obvious without agent deliberation.

## Compiled Truth

### Mount Roles and When to Use Each

The codebase (`src/ravn/domain/mimir.py`) defines three canonical roles with conventional read priorities:

| Role | `read_priority` | When to use | Who writes |
|---|---|---|---|
| `local` | 0 (highest) | In-progress notes, scratch work, session-scoped drafts | Agent freely |
| `shared` | 1 | Vetted cross-agent knowledge, final research pages | Agent via explicit override after quality check |
| `domain` | 2 | External/specialized corpora (e.g. codebase snapshots, vendor docs) | Rarely — usually ingested by operators |

**Decision heuristic:**
1. Is the content ephemeral or draft-quality? → `local`
2. Is it vetted, well-sourced, and useful to other agents? → `shared`
3. Is it a read-only external corpus? → `domain` (operator-managed, agents read only)

### Recommended Routing Model

The `CompositeMimirAdapter` (`src/ravn/adapters/mimir/composite.py`) implements a three-tier write routing model that should be the standard for Niuu:

**Tier 1 — Explicit agent override** (`mimir="shared"`)
The `mimir_write` tool accepts an optional `mimir` parameter. When set, it bypasses all routing rules and writes directly to the named mount. Use this for deliberate promotion (e.g. "I'm confident this is ready for shared knowledge").

**Tier 2 — Category-prefix rules** (configured via `WriteRouting`)
Path prefixes map to mount names. First match wins:
```
("self/",       ["local"])
("research/",   ["local", "shared"])
("technical/",  ["local", "shared"])
("household/",  ["shared"])
```
Agents writing to `research/` get dual-write (local + shared) automatically.

**Tier 3 — Default fallback**
Any path not matched by a prefix rule goes to `local`. This is the safe default — no unintended promotion.

**Read fan-out** is transparent: `CompositeMimirAdapter` queries all mounts in priority order and deduplicates by page path. Agents call `mimir_search` and `mimir_read` without thinking about which mount holds the data.

### Naming and Description Scheme

Names and descriptions are the primary signals an agent uses when choosing mounts. Make them self-explanatory:

| Mount name | `desc` in registry | What to write there |
|---|---|---|
| `local` | "This session's scratch pad — ephemeral, not shared" | Drafts, working notes, in-progress research |
| `shared` | "Organization-wide knowledge base — vetted pages only" | Finalized research, decisions, entity pages |
| `domain-{corpus}` | "Read-only corpus: {corpus description}" | Never write; read for background context |

The `mimir_write` tool description already models this pattern:
> "Use the optional 'mimir' parameter to route the write to a specific instance (e.g. 'shared' to promote to the shared Mímir), bypassing category routing."

This phrasing should be preserved — it teaches the agent the promotion idiom without requiring it to learn routing internals.

### Implicit vs. Explicit vs. Server-Name-Based Selection

| Mechanism | How it works | Best for |
|---|---|---|
| **Implicit (category prefix)** | `WriteRouting` rules match path prefix → mounts | Common cases where path encodes intent |
| **Explicit (`mimir=` param)** | Agent passes mount name directly | Deliberate promotion to `shared`; exceptional routing |
| **Server-name-based (separate MCP servers)** | Agent picks which MCP server to call | Not recommended (see trade-offs below) |

Recommendation: **implicit routing as the default, explicit override for promotion**. Never require agents to remember server names or call different MCP endpoints for different mounts.

### Multiple MCP Servers vs. One Composite

| Dimension | Multiple MCP servers | One composite MCP server |
|---|---|---|
| Agent cognitive load | High — agent must choose which server | Low — one `mimir_write` tool, one `mimir_search` |
| Read transparency | Agent must fan out manually or miss mounts | Transparent fan-out at adapter layer |
| Misconfiguration risk | Agent may write to wrong server; silent data loss | Routing misconfiguration is centralized and auditable |
| Operational complexity | N servers to deploy, monitor, auth | One server; internal mount config |
| Tool namespace pollution | 6 tools × N servers = N×6 tool slots consumed | Always 6 tools regardless of mount count |
| Routing auditability | Distributed; hard to inspect | `WriteRouting.rules` is a single inspectable list |

**Verdict: always use one composite MCP server.** Multiple MCP servers are only justified if mounts have completely disjoint auth domains that cannot be bridged server-side (e.g. different SPIFFE trust domains with no federation).

### Risks and Edge Cases

**Silent write loss**
If a mount name in `WriteRouting` does not match any mount in `_mount_map`, `CompositeMimirAdapter.upsert_page` logs a warning and silently skips the mount. Agents never see an error. Mitigation: validate routing config at startup; add a health check that reads back a just-written test page.

**Dual-write consistency**
When a path prefix routes to both `local` and `shared`, partial failure (one mount down) produces split state. An agent reading back may get the `local` version (priority 0) even though the `shared` write failed. Mitigation: treat dual-write as best-effort; require agents to use `mimir_lint` after bulk writes to detect divergence.

**Stale `local` shadowing `shared`**
If `local` has an outdated version of a page and `shared` has the canonical version, the `local` copy always wins (priority 0). Agents may work from stale data. Mitigation: periodically purge or sync `local` scratch content; use `mimir_lint` L08 (stale content) to flag old local pages.

**Explicit override bypasses all safety**
`mimir="domain"` would write directly to a domain mount even if it is meant to be read-only. There is no write-guard at the adapter level. Mitigation: operators should configure domain mounts with a no-op `upsert_page` that raises `PermissionError`, or use a read-only `HttpMimirAdapter` pointing to a read-only endpoint.

**Tool description drift**
If mount descriptions in the registry diverge from actual routing rules, agents will make wrong decisions. Mitigation: keep `desc` fields in `MimirRegistryEntry` synchronized with `WriteRouting` rules; treat the registry as the single source of truth for human-readable mount semantics.

**Agent over-promotion**
An agent that applies `mimir="shared"` too freely floods the shared knowledge base with low-quality content. Mitigation: require `produced_by_thread: true` and non-empty `source_ids` in frontmatter for any `research/` page (already enforced by `MimirWriteTool._validate_research_page_provenance`); extend this check to `shared` mount writes.

### Recommendation for Niuu

1. **Deploy one composite Mimir MCP server per agent persona** — never expose raw per-mount MCP servers to agents.
2. **Use three mounts** — `local` (priority 0), `shared` (priority 1), and optionally one `domain-*` per specialized corpus.
3. **Write routing config**:
   ```python
   WriteRouting(
       rules=[
           ("self/",       ["local"]),
           ("research/",   ["local", "shared"]),
           ("technical/",  ["local", "shared"]),
       ],
       default=["local"],
   )
   ```
4. **Name mounts with role-first names** (`local`, `shared`, `domain-{name}`) and populate `desc` in the registry with one sentence stating what belongs there.
5. **Teach promotion via tool description**, not separate tools or servers — the `mimir=` parameter is the right mechanism.
6. **Guard domain mounts at the adapter layer** — return `PermissionError` from `upsert_page` on any mount that should be read-only.

## Timeline

- 2026-05-05: Audited `CompositeMimirAdapter`, `WriteRouting`, `MimirMount`, `MimirWriteTool`, `MimirRegistryEntry`, and `MimirMcpServer` in the Volundr repo. No existing Mimir page covered mount selection. [Source: niuulabs/volundr codebase, 2026-05-05]
- 2026-05-05: Synthesized routing model, naming scheme, trade-off table, and risks from code analysis. Confirmed composite-server approach is already the production implementation. [Source: NIU-775 task, 2026-05-05]

## Sources

- `src/ravn/adapters/mimir/composite.py` — CompositeMimirAdapter: fan-out reads, routed writes (niuulabs/volundr, retrieved 2026-05-05)
- `src/ravn/domain/mimir.py` — MimirMount, WriteRouting: role, read_priority, routing resolution (niuulabs/volundr, retrieved 2026-05-05)
- `src/ravn/adapters/tools/mimir_tools.py` — MimirWriteTool: explicit mimir= override, research provenance validation (niuulabs/volundr, retrieved 2026-05-05)
- `src/mimir/mcp.py` — MimirMcpServer: six tools, single server wrapping MimirPort (niuulabs/volundr, retrieved 2026-05-05)
- `src/mimir/registry.py` — MimirRegistryEntry: name, role, desc, default_read_priority fields (niuulabs/volundr, retrieved 2026-05-05)

<!-- sources: src_niu775_composite_adapter, src_niu775_domain_models, src_niu775_mimir_tools, src_niu775_mcp_server, src_niu775_registry -->
