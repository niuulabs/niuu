# Architecture Rules

## Hexagonal Architecture

All infrastructure is abstracted behind **shared ports** (interfaces). Adapters implement these ports. Business logic (regions) never imports infrastructure directly.

```
src/buri/
├── ports/      # Interfaces (abstract base classes)
├── adapters/   # Implementations of ports
└── regions/    # Business logic (the six regions)
```

## Layer Rules

- **Regions** import from `ports/` only, NEVER from `adapters/`
- **Adapters** import from `ports/` for interfaces they implement
- **CLI/main** imports from everywhere (it's the composition root)

## The Six Regions

| Region | Function | Cycle Time | Model Size |
|--------|----------|------------|------------|
| **Sköll** | Rapid perception, threat detection, interrupts | ~1s | Nano |
| **Hati** | Pattern recognition, analysis, classification | ~5s | Medium |
| **Sága** | Memory, continuity, keeper of self (Minni) | ~10s | Medium + Vector |
| **Móði** | Deliberate reasoning, planning, decisions | ~30s | Large |
| **Váli** | Creative thinking, alternatives, dreaming | ~5min | Large (high temp) |
| **Víðarr** | Meta-cognition, self-observation, calibration | ~5s | Medium |

## Communication

- **Synapses (nng)** — All inter-region communication (~10-50μs latency)
- **Distributed Blackboard** — Shared state (attention, felt sense, working memory)
- **Files** — Persistence only (Minni YAML, PID files, logs)

No Redis. No external state store. Just nng for communication and files for persistence.

## Authentication & Authorization

- **Never build custom auth/token layers** — always delegate to standard OIDC/OAuth2 flows
- **IDP-agnostic** — code must not be coupled to a specific identity provider (Keycloak, Entra ID, Okta, etc.). Use the identity adapter pattern to abstract the IDP
- All authentication goes through Envoy + the configured IDP in production
- Service-to-service auth uses standard OIDC flows (e.g. `client_credentials` grant), not internal bypasses or custom tokens

### Exception: Personal Access Tokens (PATs)

PATs are an intentional exception to the "no custom tokens" rule. Ting's autonomous
dispatcher must call Volundr as a specific user without an active browser session.
PATs are long-lived JWTs signed with the same symmetric key that Envoy validates,
so they integrate with the existing infrastructure without requiring IDP changes.
The shared PAT code lives in `src/niuu/` (service, port, adapter, model).

### Exception: scoped workload tokens (workload identity)

The same sanctioned exception covers the short-lived tokens minted by the
workload-identity exchange (`POST /api/v1/tokens/workload/exchange`). When the
exchange request asks for `scopes`, the issued JWT carries
`token_use: "valkyrie_build"` and a `scopes` claim bounded to
`KNOWN_WORKLOAD_SCOPES` (`src/niuu/domain/services/token_scope.py`) — a
least-privilege credential that can do one named thing and nothing else,
enforced fail-closed at that entry point (Forge session create, Ting workflow
launch, Observatory topology push). Tokens without
`token_use == "valkyrie_build"` are never scope-checked, so human sessions and
ordinary PATs are unaffected.

The `valkyrie_build` claim value is historical — builds were the first use.
It means "this credential is scoped", not "this credential builds"; the
mechanism is general and scopes are not limited to builds.

`KNOWN_WORKLOAD_SCOPES` is deliberately a constant, not configuration: it is
the allowlist that stops a caller self-granting privilege, so anything able to
edit it can mint authority. A scope is also only real because code enforces
it — `require_scope("x")` on a route is what gives `"x"` meaning, so a
config-only scope would grant nothing while reading as protection.

Known limitation (deliberate, for now): PATs themselves cannot carry scopes —
a leaked PAT retains its owner's full authority. Off-cluster Valkyries using
a PAT via `external_token_env` therefore do not get least-privilege; scoped
PAT minting is future work. When adding a NEW scoped entry point, add its
scope to `KNOWN_WORKLOAD_SCOPES` and a `require_scope(...)` dependency on the
route — scoped tokens are only as narrow as the enforcement coverage.
`tests/test_niuu/test_token_scope.py` fails if the two drift apart in either
direction.
