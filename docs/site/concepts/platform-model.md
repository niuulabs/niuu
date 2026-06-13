# Platform Model

Understand how Niuu is organized.

Niuu is a platform of cooperating services. Each service owns a clear part of the operator workflow, and the web UI composes those services into one control surface.

## Core services

| Service | Responsibility |
| --- | --- |
| Völundr | Sessions, workspaces, launch presets, git review, and remote dev pods |
| Skuld | Live broker inside session environments for chat, terminal, tools, and events |
| Ting | Workflow orchestration, dispatch, staged runs, and review loops |
| Ravn | Personas, assistants, triggers, budgets, and resident agent behavior |
| Mímir | Shared memory, sources, knowledge pages, search, and wardens |
| Bifröst | Model catalog, provider health, aliases, routing, and usage telemetry |
| Guild | Registry of runtime instances and service capabilities |
| Observatory | Topology, registry visibility, and event context |
| Sleipnir | Transport abstraction for local and distributed messaging |

## Operator model

Operators can inspect, interrupt, redirect, approve, and review work across the platform. Niuu is designed around visible state and explicit control instead of hidden background automation.
