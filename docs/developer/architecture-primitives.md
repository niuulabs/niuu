# Architecture Primitives

This is a factual map of current Niuu boundaries. Before adding a new boundary,
read `.claude/rules/reuse-before-build.md` and inspect the relevant primitive
here.

## Reuse Before Build

PRs that add a new port, adapter, runner, executor, transport, registry,
gateway, service, workflow layer, scheduler, or framework must include a
**Reuse Before Build** section. Name the existing primitives inspected, why each
was insufficient, why the new boundary is genuinely different, why it is not a
parallel implementation, and how it composes with the existing architecture.

## Model Calls

- Ravn model calls use `ravn.ports.llm.LLMPort`.
- Current Ravn adapters include Anthropic, OpenAI-compatible, Bifrost, command,
  and fallback adapters under `src/ravn/adapters/llm/`.
- Ting has its own `ting.ports.llm.LLMPort` and a Bifrost adapter for dispatcher
  services.
- Bifrost is the model gateway/catalog package. Use it when the work belongs to
  central model routing or accounting, not when a local caller only needs
  Ravn's `LLMPort`.

## Resident State

- Durable resident state uses `ravn.domain.resident_state.ResidentStatePort`.
- Concrete resident-state adapters live under `src/ravn/adapters/resident_state/`.
- `select_resident_state()` chooses an available adapter from configured
  candidates. Callers should not branch on Mimir, GBrain, or local-file details.

## Resident Signals

- Resident signal loading uses `ravn.ports.resident_signal.ResidentSignalSourcePort`.
- Attention candidate listing uses `ResidentSignalCandidateSourcePort`.
- Concrete sources normalize data into `ResidentInboxSignal` envelopes before
  Momentum or other consumers interpret them.
- `docs/developer/resident-signals.md` has the source-specific guidance.

## Executor Handoff And Agent Execution

- Persona execution uses `ravn.ports.executor.ExecutorPort` to build an
  `ExecutionAgentPort`.
- `ravn.adapters.executors.agent.AgentExecutor` builds the normal in-process
  Ravn agent.
- `ravn.adapters.executors.cli.CliTransportExecutor` adapts Ravn execution onto
  the Skuld CLI transport stack.
- Skuld transports under `src/skuld/transports/` own concrete Codex, Claude,
  OpenCode, subprocess, WebSocket, and remote-control communication.
- Use this stack for Codex/Claude/OpenCode/Ravn-style executor handoff. Do not
  create a product-specific command executor unless the PR proves the existing
  executor and transport primitives cannot carry the request.

## Human Involvement

- Agent-initiated questions use `ravn.adapters.tools.ask_user.AskUserTool`.
- Skuld transports can surface `AskUserQuestion` and permission requests to a
  client when the transport supports that path.
- Resident operator-needed state is part of `ResidentStatePort`.
- Permission and checkpoint boundaries live in `ravn.ports.permission` and
  `ravn.ports.checkpoint`. Reuse those before inventing a new human-review lane.

## Repeatable Workflows

- Ting owns persisted workflow definitions through
  `ting.ports.workflow_repository.WorkflowRepository` and
  `ting.adapters.postgres_workflows.PostgresWorkflowRepository`.
- Workflow campaign persistence uses `WorkflowCampaignRepository`.
- Ravn exposes workflow tools through `src/ravn/adapters/tools/workflow_tools.py`
  and can discover workflow capabilities through `WorkflowCapabilityPort`.
- Skuld has workflow-trigger and workflow-gate runtime code in the broker. It is
  messy and broad, but it is the existing workflow primitive. Do not add a new
  workflow engine without proving this path cannot be reused or narrowed.

## Capability Growth

- Capability catalog types live in `ravn.domain.capability_catalog`.
- Capability discovery ports live in `ravn.ports.capability`.
- Tool-building uses `ravn.ports.tool_build_backend.ToolBuildBackend` and the
  adapters under `src/ravn/adapters/tool_build/`.
- Valkyrie learning and promotion code lives under `src/ravn/valkyrie_evolution/`
  with Odin review support under `src/ravn/odin/`.
- This area is intentionally cautious. Do not add fake autonomy, placeholder
  capability registries, or parallel promotion systems.

## Momentum Cognitive Artifacts

- Momentum typed artifacts live in `src/ravn/momentum/models.py`.
- Momentum parsing/rendering lives in `src/ravn/momentum/render.py`.
- Momentum state compaction and patch helpers live in `src/ravn/momentum/state.py`
  behind `ravn.ports.momentum_state_compactor.MomentumStateCompactorPort`.
- Momentum orchestration lives in `src/ravn/momentum/pipeline.py`.
- Momentum persists through `ResidentStatePort` and loads resident signals
  through `ResidentSignalSourcePort`; it should not hardwire concrete Mimir,
  GBrain, Codex, Claude, OpenCode, or local-file implementations.
