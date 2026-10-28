# Canonical Ravn harness contract

Ravn owns the harness boundary: one provider-neutral runtime that executes an
agent turn.  This does not move session, channel, or CLI-transport ownership
from Skuld or Niuu.  Niuu continues to own reusable `CLITransport` mechanics;
a Ravn adapter translates those mechanics into this Ravn-owned contract.

## Contract

`ravn.ports.executor.RavnHarnessPort` is the canonical port.  It has these
operations:

1. `start()` initializes a reusable runtime. It is idempotent.
2. `run_turn(HarnessTurnInput)` executes exactly one turn after startup and
   returns `HarnessTurnResult`.
3. `control(HarnessControl)` applies an interrupt or steering request only to
   an active turn. It returns `False` when that control is not declared by the
   profile, rather than guessing a degraded operation.
4. `close()` releases the runtime. It is idempotent.

A turn input contains user text, zero or more opaque byte attachments, and an
opaque provider session identifier for a resume-capable runtime.  A turn result
contains the existing `TurnResult`, an optional provider session identifier,
reasoning when the runtime exposes it, and string metadata.  This retains the
existing result and tool-error semantics: tool outcomes remain in
`TurnResult.tool_results`; cancellation, provider, and runtime failures are
raised and never represented as successful turn results.

`HarnessCapabilityProfile` is part of the port, not an adapter detail. It
contains only features Ravn consumes:

- Ravn tool execution and dynamic tool exposure;
- provider reasoning exposed to the harness;
- attachment and image input;
- interrupt and steering mode;
- opaque provider-session resume.

Profiles validate eagerly. Images require attachment support, dynamically
exposed tools require tool execution, and `interrupt_resume` steering requires
interrupt support. Invalid profiles raise `HarnessContractError`
deterministically at construction. A typed input requesting a capability not
in the profile raises `UnsupportedHarnessCapabilityError`; calling a lifecycle
operation at the wrong time raises `HarnessLifecycleError`.

## Capability-aware selection

Persona executor bindings may declare `requires` using only the canonical
names `tools`, `reasoning`, `attachments`, `images`, `interrupt`, `steering`,
`session_resume`, and `dynamic_tools`. Ravn validates these names when loading
the persona and compares all of them with the selected `HarnessCapabilityProfile`
before building or starting a provider session. An incompatible explicit
binding raises a diagnostic naming the harness and every missing capability.

An executor binding can declare `fallbacks` only as explicit, pre-start
alternatives. Ravn considers one only when the primary profile cannot meet the
requirements, and selects it only when it meets the complete set; it never
uses a fallback after a provider/session failure. The resolved adapter, runtime
identity, model, capability set, requirements, and reason are available on the
executor's `harness_selection` and emitted as `ravn.harness.selection` without
credentials or prompt content.

## Current mapping and migration

The existing `ExecutionAgentPort` remains the compatibility interface for
current CLI, gateway, and drive-loop callers. It takes a string in `run_turn`,
exposes structural attributes, and has separate `interrupt`/`steer` methods.
It is intentionally unchanged by this ticket, so current public behavior is
unchanged.

| Current path | Canonical profile | Mapping |
| --- | --- | --- |
| In-process `RavnAgent` | `HarnessCapabilityProfile.native_ravn_agent()` | Tools, reasoning, interrupt, and dynamic `register_tool` are available. The current string-turn API has no attachment/image input, no provider-session resume, and no live steering. |
| CLI-backed `CliTransportAgent` | `HarnessCapabilityProfile.cli_backed(...)` | The Ravn adapter supplies the declared transport facts. Tool use remains Ravn's MCP tool surface; resume, interrupt, and steering are declared from transport capabilities. Dynamic tools and attachments/images remain false unless the adapter can actually provide them. |

No profile selection is based on a concrete native-agent or CLI transport
class. A composition root selects an adapter and that adapter declares its
profile. NIU-1123 migrates the current native and CLI callers to
`RavnHarnessPort`; it must preserve the compatibility behavior above while
wrapping the two existing paths.
