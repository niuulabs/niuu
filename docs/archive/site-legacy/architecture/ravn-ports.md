# Ravn Extension Points (Ports)

Ravn uses hexagonal architecture. Every pluggable subsystem is defined as an
**abstract port** (Python ABC) in `src/ravn/ports/`. Concrete implementations
(**adapters**) live in `src/ravn/adapters/`. The composition root
(`src/ravn/cli/commands.py`) wires them together at startup using config-driven
dynamic import — point a config key at a fully-qualified class path, and Ravn
loads it without any code changes.

## How to add a custom adapter

1. Write a class that inherits from the relevant port ABC.
2. Implement all `@abstractmethod` members.
3. Point the corresponding config key at your class using its fully-qualified
   dotted path. No other code changes are needed.

```yaml
# ravn.yaml
llm:
  providers:
    - adapter: mycompany.llm.PrivateLLMAdapter
      kwargs:
        endpoint: "https://llm.internal/"
      secret_kwargs_env:
        api_key: MY_LLM_API_KEY
```

---

## Port Reference

### LLMPort

**File:** `src/ravn/ports/llm.py`
**Config key:** `llm.providers[].adapter`
**Cardinality:** one or more — providers form a priority list, falling through to the next on error

`LLMPort` is the inference backend. Every time the agent needs to think — whether
streaming a response to the user, calling tools, or running an internal
single-shot generation — it goes through this port. You would implement a custom
`LLMPort` to connect Ravn to a private model endpoint, a fine-tuned model, a
proxy that injects audit logging, a cost-capped router, or any provider not
covered by the built-in adapters. The `stream` method is used for all
interactive turns where partial output should appear as it arrives; `generate`
is used for internal single-shot calls (e.g. memory extraction, fact summarisation)
where streaming is not needed. Multiple providers can be defined — the
`FallbackLLMAdapter` wraps them in order and promotes to the next provider on
any error, giving you automatic redundancy across providers or regions.

| Abstract method | Signature | Notes |
|-----------------|-----------|-------|
| `stream` | `(messages, tools, system, model, max_tokens, thinking) → AsyncIterator[StreamEvent]` | Streaming inference |
| `generate` | `(messages, tools, system, model, max_tokens, thinking) → LLMResponse` | Single-shot inference |

Optional overrides: `supports_thinking` (property, default `False`).

Built-in adapters: `AnthropicAdapter`, `BifrostAdapter`, `OpenAICompatibleAdapter`, `FallbackLLMAdapter`.

---

### ToolPort

**File:** `src/ravn/ports/tool.py`
**Config key:** `tools.custom[].adapter`
**Cardinality:** any number — all registered tools are presented to the LLM each turn

`ToolPort` is the extension point for anything you want the agent to be able to
*do*. Each tool has a name, a description shown to the LLM, a JSON Schema
defining its inputs, a required permission level, and an async `execute` method
that carries out the action and returns a result. The LLM decides when and how to
call tools based on the description and schema — good descriptions matter.

You would implement a custom `ToolPort` to expose internal APIs to the agent
(deploy a service, query a database, send a notification), wrap company-specific
CLI tools, create domain-specific file operations, or add any capability not
covered by the built-ins. Tools can declare themselves non-parallelisable (e.g.
`git_commit`) to prevent the agent from running them concurrently with other
tools. The `diff_preview` method lets file-modifying tools show a diff before
writing, which is displayed in all output channels.

Tools are filtered by persona (`allowed_tools`, `forbidden_tools`) and by
permission mode, so the same tool can be available in one deployment and blocked
in another without changing the tool itself.

| Abstract member | Type | Notes |
|-----------------|------|-------|
| `name` | property → `str` | Unique identifier used by the LLM |
| `description` | property → `str` | Shown to the LLM to guide usage |
| `input_schema` | property → `dict` | JSON Schema for inputs |
| `required_permission` | property → `str` | Permission gate |
| `execute` | `async (input: dict) → ToolResult` | Core logic |

Optional overrides: `parallelisable` (default `True`), `diff_preview(input)`.

Built-in tools: `read_file`, `write_file`, `edit_file`, `bash`, `web_fetch`, `git_*`,
`todo_write`, `ask_user`, and many more (see `adapters/tools/`).

---

### MemoryPort / BuriMemoryPort

**File:** `src/ravn/ports/memory.py`
**Config key:** `memory.backend`
**Cardinality:** exactly one (optional — Ravn runs without memory if unset)

`MemoryPort` gives the agent persistent memory across sessions. Without a memory
backend the agent is stateless — each session starts blank. With one, the agent
can recall relevant past interactions, surface what it remembers about entities
and topics, and build continuity across conversations and daemon tasks.

The core contract is `record_episode` (write a completed turn into memory) and
`query_episodes` (retrieve semantically similar past turns given a query). The
`prefetch` method is called at the start of each turn to inject a memory summary
into the system prompt automatically. `inject_shared_context` / `get_shared_context`
allow state to be shared across concurrent sessions running on the same node.

Memory adapters can also expose additional tools to the agent via `extra_tools()`
— the Buri adapter uses this to give the agent explicit tools for recalling facts,
managing knowledge graphs, and updating session state. This means a memory adapter
is more than a database; it can actively shape what the agent knows and can do.

You would implement a custom `MemoryPort` to connect to a specific vector database
(Pinecone, Weaviate, Qdrant), integrate with an existing knowledge store, or build
a specialised memory strategy (e.g. only remember events matching certain criteria,
or store memories in a team-shared backend rather than per-agent).

`BuriMemoryPort` extends `MemoryPort` with a typed knowledge graph: facts about
entities, supersession (newer facts override older ones), and relationship traversal.
Implement `BuriMemoryPort` rather than `MemoryPort` when you need structured,
queryable knowledge rather than raw episode recall.

**MemoryPort** abstract methods:

| Method | Signature |
|--------|-----------|
| `record_episode` | `async (episode: Episode) → None` |
| `query_episodes` | `async (query, limit, min_relevance) → list[EpisodeMatch]` |
| `prefetch` | `async (context) → str` |
| `search_sessions` | `async (query, limit) → list[SessionSummary]` |
| `inject_shared_context` | `(context: SharedContext) → None` |
| `get_shared_context` | `() → SharedContext \| None` |

Optional overrides: `extra_tools(session_id)`, `process_inline_facts(...)`, `on_turn_complete(...)`.

**BuriMemoryPort** extends `MemoryPort` with typed fact-graph methods:
`ingest_fact`, `query_facts`, `supersede_fact`, `forget_fact`,
`get_relationships`, `build_knowledge_context`, `update_session_state`.

Built-in adapters: `SqliteMemoryAdapter`, `PostgresMemoryAdapter`, `BuriMemoryAdapter`.

---

### ChannelPort

**File:** `src/ravn/ports/channel.py`
**Config key:** wired programmatically in the composition root
**Cardinality:** one per session, often composite (multiple channels wrapped as one)

`ChannelPort` is the output surface — where the agent's events go. Every
`RavnEvent` the agent emits (text chunks, tool calls, tool results, status
updates, errors) flows through the active channel. The interface is intentionally
minimal: a single `emit` method. The channel decides what to do with each event —
print it to the terminal, stream it to a WebSocket, forward it to RabbitMQ,
capture it for testing, or silently discard it.

You would implement a custom `ChannelPort` to integrate with a proprietary
notification system, fan events out to multiple sinks simultaneously
(`CompositeChannel` wraps multiple channels and fans events to all of them), or
add event filtering/transformation before delivery. In daemon mode, the channel
is typically `SleipnirChannel` (events go to the ODIN backbone) or
`GatewayChannel` (events flow to a messaging platform via `GatewayChannelPort`).

| Abstract method | Signature |
|-----------------|-----------|
| `emit` | `async (event: RavnEvent) → None` |

Built-in adapters: `SilentChannel` (discards everything, useful for background tasks),
`GatewayChannel` (bridges to a `GatewayChannelPort`), `CaptureChannel` (buffers
events in memory, used in tests and single-shot runs), `CompositeChannel` (fans
out to multiple channels), `SleipnirChannel` (publishes events to RabbitMQ).

---

### GatewayChannelPort

**File:** `src/ravn/ports/gateway_channel.py`
**Config key:** `gateway.channels[].adapter`
**Cardinality:** any number — all configured gateways run concurrently

`GatewayChannelPort` is the bidirectional messaging platform adapter. Where
`ChannelPort` is one-directional (agent → output), a gateway is two-directional:
it receives messages from users on an external platform (Telegram, Discord, Slack,
a custom HTTP endpoint) and routes them to the agent, and it sends the agent's
responses back to the right chat, user, or room.

Each gateway runs as its own long-lived asyncio task. When a message arrives, the
gateway calls the registered `on_message` handler (wired by the composition root
to the agent factory), which creates a new agent session and processes the message.
The gateway receives back the agent's response events and calls `send_text`,
`send_image`, or `send_audio` to deliver them.

You would implement a custom `GatewayChannelPort` to add support for a new
messaging platform (Teams, WhatsApp Business API, a custom internal chat tool,
IRC, SMS), integrate with a ticketing system where messages come in as tickets,
or add middleware behaviour like message queuing, rate limiting per user, or
multi-tenancy routing.

Multiple gateways can be active simultaneously — a single Ravn daemon can serve
Telegram users, a Slack workspace, and an HTTP API at the same time.

| Abstract method | Signature |
|-----------------|-----------|
| `start` | `async () → None` |
| `stop` | `async () → None` |
| `send_text` | `async (chat_id, text) → None` |
| `send_image` | `async (chat_id, image_bytes, caption) → None` |
| `send_audio` | `async (chat_id, audio_bytes) → None` |
| `on_message` | `(handler: MessageHandler) → None` |

Built-in adapters: `TelegramGateway`, `HttpGateway`, `DiscordGateway`,
`SlackGateway`, `MatrixGateway`, `WhatsAppGateway`.

---

### TriggerPort

**File:** `src/ravn/ports/trigger.py`
**Config key:** `initiative.trigger_adapters[].adapter`
**Cardinality:** any number — all triggers run concurrently as asyncio tasks

`TriggerPort` is how the agent initiates work autonomously, without a human
sending a message. A trigger is a long-lived asyncio task that runs for the
lifetime of the daemon. Whenever it decides a task should fire — a cron schedule
fires, an event arrives on the message bus, a monitored condition becomes true —
it calls `enqueue()` with an `AgentTask`, and the drive loop picks it up and runs
it.

You would implement a custom `TriggerPort` to fire tasks from any source that
matters to your deployment: a Kubernetes event watch, a file system watcher, a
CI/CD webhook, a monitoring alert, a database change feed, an IoT sensor, or a
custom business event. The trigger only needs to know how to detect the condition
and construct an `AgentTask` — the drive loop handles concurrency, prioritisation,
persistence, and execution.

Multiple triggers run simultaneously. Each fires independently into the shared
task queue, so a cron trigger and a webhook trigger can both be active and will
interleave gracefully.

| Abstract member | Type |
|-----------------|------|
| `name` | property → `str` |
| `run` | `async (enqueue: Callable[[AgentTask], Awaitable[None]]) → None` |

```python
class MyWebhookTrigger(TriggerPort):
    def __init__(self, port: int = 9000) -> None:
        self._port = port

    @property
    def name(self) -> str:
        return "webhook"

    async def run(self, enqueue):
        async for payload in listen_http(self._port):
            await enqueue(AgentTask(initiative_context=payload["task"]))
```

```yaml
initiative:
  trigger_adapters:
    - adapter: mypackage.WebhookTrigger
      kwargs:
        port: 9000
```

Built-in adapters: `CronTrigger` (cron expressions, natural language schedules),
`SleipnirEventTrigger` (RabbitMQ routing-key pattern matching),
`ConditionPollTrigger` (polls a sensor agent and fires when it returns `TRIGGER`),
`MimirSourceTrigger` (fires when unprocessed knowledge sources are waiting),
`MimirStalenessTrigger` (fires when frequently-accessed wiki pages become stale).

---

### SlashCommandPort

**File:** `src/ravn/ports/slash_command.py`
**Config key:** `slash_commands[].adapter`
**Cardinality:** any number — all registered commands are available in every session

`SlashCommandPort` lets users control and inspect the running agent without
breaking the conversational flow. A slash command is invoked by typing `/name`
(with optional arguments) in the REPL or in a gateway chat. It runs
synchronously, returns a formatted string, and never touches the agent's message
history — it's a side channel for operator commands, not part of the conversation.

You would implement a custom `SlashCommandPort` to expose deployment-specific
operator actions: trigger a deployment, show internal system status, flush a
cache, list active jobs, or inject a specific context into the next turn. Custom
commands are registered after built-ins, so they override built-in names on
collision if needed (e.g. to replace `/status` with a richer version).

The `SlashCommandContext` passed to `handle` contains the current session,
loaded tools, iteration budget, and checkpoint port, so commands can inspect and
mutate live agent state.

| Abstract member | Type |
|-----------------|------|
| `name` | property → `str` (e.g. `"/deploy"`, must include the slash) |
| `handle` | `(args: str, ctx: SlashCommandContext) → str` |

Optional overrides: `aliases` (property, default `[]`), `description` (property,
default `""` — shown in `/help`).

```python
class DeployCommand(SlashCommandPort):
    @property
    def name(self) -> str:
        return "/deploy"

    @property
    def description(self) -> str:
        return "trigger a production deployment"

    def handle(self, args: str, ctx: SlashCommandContext) -> str:
        env = args.strip() or "staging"
        trigger_deploy(env)
        return f"Deployment to {env} triggered."
```

```yaml
slash_commands:
  - adapter: mypackage.DeployCommand
```

Built-in commands: `/help`, `/tools`, `/memory`, `/compact`, `/budget`, `/todo`,
`/status`, `/skills`, `/init`, `/checkpoint`.

---

### PersonaPort

**File:** `src/ravn/ports/persona.py`
**Config key:** `persona_source.adapter`
**Cardinality:** exactly one

`PersonaPort` is the source of persona configurations. A persona defines the
agent's identity for a given context: its system prompt template, which tool
groups are allowed or forbidden, the default permission mode, LLM alias, and
iteration budget. Different personas make the same Ravn installation behave
very differently — `coding-agent` writes code and runs tests; `mimir-curator`
synthesises knowledge wiki pages; `autonomous-agent` has full access and no
iteration limit.

By default, personas are loaded from YAML files in `~/.ravn/personas/` with a
set of built-in personas as fallback. You would implement a custom `PersonaPort`
to load personas from a database (so they can be edited via a UI without touching
files), derive them dynamically from the incoming request context, fetch them from
a remote configuration service, or enforce organisational policies on what personas
are permitted in a given deployment.

The persona source is queried at session start (and when the drive loop starts a
triggered task) to resolve a named persona. It is not queried on every turn.

| Abstract method | Signature |
|-----------------|-----------|
| `load` | `(name: str) → PersonaConfig \| None` |
| `list_names` | `() → list[str]` |

Default adapter: `PersonaLoader` (YAML files at `~/.ravn/personas/` + built-ins).

```yaml
persona_source:
  adapter: mycompany.ravn.DbPersonaAdapter
  secret_kwargs_env:
    dsn: PERSONA_DB_DSN
```

---

### ProfilePort

**File:** `src/ravn/ports/profile.py`
**Config key:** `profile_source.adapter`
**Cardinality:** exactly one

`ProfilePort` is the source of deployment profiles. A profile defines the
deployment identity of a Ravn node: its name, physical location, which persona it
runs by default, what infrastructure it connects to (Mímir mounts, MCP servers,
gateway channels, Sleipnir topics), its cascade mode, and whether checkpointing
is enabled. Profiles are how you tell a specific Ravn node what *it* is — the
`tanngrisnir` profile might describe a Kubernetes-hosted node with full
infrastructure access, while `huginn` describes a mobile node with limited tools.

By default, profiles are YAML files in `~/.ravn/profiles/`. You would implement a
custom `ProfilePort` to load profiles from a Kubernetes ConfigMap (so profiles
follow the node's deployment), from a central control plane that assigns profiles
to nodes at registration time, or to generate profiles programmatically from
environment variables or node metadata.

A profile is resolved once at daemon startup and does not change during the
daemon's lifetime.

| Abstract method | Signature |
|-----------------|-----------|
| `load` | `(name: str) → RavnProfile \| None` |
| `list_names` | `() → list[str]` |

Default adapter: `ProfileLoader` (YAML files at `~/.ravn/profiles/` + built-ins).

```yaml
profile_source:
  adapter: mycompany.ravn.K8sConfigMapProfileAdapter
  kwargs:
    namespace: ravn-system
```

---

### CheckpointPort

**File:** `src/ravn/ports/checkpoint.py`
**Config key:** `checkpoint.adapter`
**Cardinality:** exactly one (optional — checkpointing disabled if unset)

`CheckpointPort` provides crash recovery and manual session snapshots. Ravn uses
two overlapping concepts: a *crash-recovery checkpoint* (one per active task,
overwritten on every tool call) and *named snapshots* (multiple per task, saved
on demand or at milestones, never overwritten). If the daemon crashes mid-task,
the crash-recovery checkpoint lets it resume from the last safe point. Named
snapshots let the user roll back to a labelled point in the conversation (`/checkpoint restore <id>`).

You would implement a custom `CheckpointPort` to store checkpoints in a shared
database (so they survive node loss in a cluster), in object storage (S3, GCS),
or to add retention policies, encryption, or team-scoped access control. The disk
adapter is fine for single-node deployments; the Postgres adapter suits production
deployments where node restarts should not lose in-flight work.

| Abstract method | Notes |
|-----------------|-------|
| `save(checkpoint)` | Crash-recovery write — called after every tool call |
| `load(task_id)` | Load the crash-recovery checkpoint for a task |
| `delete(task_id)` | Clear the crash-recovery checkpoint on clean completion |
| `list_task_ids()` | List all task IDs with a live checkpoint |
| `save_snapshot(checkpoint) → str` | Write a named snapshot; returns its `checkpoint_id` |
| `list_for_task(task_id)` | List all named snapshots for a task |
| `load_snapshot(checkpoint_id)` | Load a named snapshot |
| `delete_snapshot(checkpoint_id)` | Delete a named snapshot |

Built-in adapters: `DiskCheckpointAdapter`, `PostgresCheckpointAdapter`.

---

### OutcomePort

**File:** `src/ravn/ports/outcome.py`
**Config key:** `evolution.outcome_adapter`
**Cardinality:** exactly one (optional)

`OutcomePort` is the long-term learning store. After every completed agent turn,
Ravn records a `TaskOutcome` — what was asked, what tools were used, whether it
succeeded, and a summary of what happened. Before starting a new task, Ravn calls
`retrieve_lessons` with the task description and injects the most relevant past
outcomes into the system prompt as lessons learned. Over time this creates a
feedback loop: the agent learns from its own history which approaches work, which
fail, and what to avoid.

You would implement a custom `OutcomePort` to store outcomes in a team-shared
database (so lessons learned by one node benefit all nodes), to feed outcomes into
a separate analytics pipeline, or to add retrieval strategies beyond simple
vector similarity (e.g. recency weighting, per-persona filtering, outcome-type
classification).

| Abstract method | Signature |
|-----------------|-----------|
| `record_outcome` | `async (outcome: TaskOutcome) → None` |
| `retrieve_lessons` | `async (task_description, limit) → str` |

Optional overrides: `count_all_outcomes()`, `list_recent_outcomes(limit, since)`.

Built-in adapter: `SQLiteOutcomeAdapter`.

---

### PermissionPort / PermissionEnforcerPort

**File:** `src/ravn/ports/permission.py`
**Config key:** `permission.mode` (simple) / `permission.enforcer` (rich)
**Cardinality:** exactly one of each

The permission system is the safety layer between the agent's intent and
execution. Every tool call is evaluated before the tool runs. There are two
levels: `PermissionPort` is a simple binary check (`check(permission) → bool`)
sufficient for coarse-grained modes like allow-all or read-only.
`PermissionEnforcerPort` is the rich evaluator used in production — it receives
the specific tool name and full arguments and returns `Allow`, `Deny`, or
`NeedsApproval`. The `NeedsApproval` path pauses the tool call, notifies the
user, and waits for an explicit approval that is then recorded so the same call
pattern doesn't require approval again.

`check_file_write` and `check_bash` are called by the file and bash tools
respectively, giving the enforcer access to the full file path or command string
for pattern-based decisions (e.g. block writes outside the workspace root, block
`rm -rf`, allow `git` commands only).

You would implement a custom `PermissionEnforcerPort` to enforce organisational
policy (block access to production credentials, require approval for all
destructive operations, log all tool calls to a compliance system), or to integrate
with an external approval workflow (PagerDuty, Slack approval bot, a custom UI).

**PermissionPort** — simple check:

| Abstract method | Signature |
|-----------------|-----------|
| `check` | `async (permission: str) → bool` |

**PermissionEnforcerPort** — rich, per-tool evaluation:

| Abstract method | Signature |
|-----------------|-----------|
| `evaluate` | `async (tool_name, args) → PermissionDecision` |
| `check_file_write` | `(path, workspace_root) → PermissionDecision` |
| `check_bash` | `(command) → PermissionDecision` |
| `record_approval` | `(tool_name, args) → None` |

Built-in adapters: `AllowAllPermission`, `DenyAllPermission`, `PermissionEnforcer`.

---

### EmbeddingPort

**File:** `src/ravn/ports/embedding.py`
**Config key:** `embedding.adapter`
**Cardinality:** exactly one (required when memory is enabled)

`EmbeddingPort` converts text into dense vector representations used for
semantic similarity search. It underpins the memory system — episodes, facts,
and session summaries are embedded when written, and queries are embedded at
retrieval time so the most semantically relevant memories surface regardless of
exact wording.

You would implement a custom `EmbeddingPort` to use a specific model that matches
your domain vocabulary (e.g. a code-focused embedding model for a coding agent, a
multilingual model for a non-English deployment), to use a self-hosted model for
data-sovereignty reasons, or to integrate with an embedding service that caches
results or batches efficiently. The `dimension` property must match the vector
column width in your memory store — changing embedding models requires re-embedding
all stored memories.

| Abstract method | Signature |
|-----------------|-----------|
| `embed` | `async (text: str) → list[float]` |
| `embed_batch` | `async (texts: list[str]) → list[list[float]]` |
| `dimension` | property → `int` |

Built-in adapters: `SentenceTransformerEmbeddingAdapter` (local, no API cost),
`OpenAIEmbeddingAdapter`, `OllamaEmbeddingAdapter`.

---

### BrowserPort

**File:** `src/ravn/ports/browser.py`
**Config key:** `browser.adapter`
**Cardinality:** exactly one (optional — browser tools disabled if unset)

`BrowserPort` gives the agent a real browser it can control. Rather than
fetching raw HTML, the browser port exposes a high-level interface: navigate to
a URL, read the accessibility tree as structured text (what the agent actually
uses for understanding the page), click elements, type into fields, scroll, take
screenshots, and run JavaScript. This lets the agent interact with SPAs,
authenticated web apps, and anything that requires JavaScript execution — not
just static pages.

You would implement a custom `BrowserPort` to use a specific browser automation
backend (Puppeteer, Selenium, a proprietary RPA tool), to pre-configure sessions
with authentication cookies, to run browsers in a sandboxed environment with
egress restrictions, or to integrate with a cloud browser service that handles
CAPTCHA solving, residential proxies, or browser fingerprint management.

| Method | Notes |
|--------|-------|
| `navigate(url, wait_for)` | Load a URL and wait for a condition |
| `snapshot()` | Accessibility tree as compact text — what the agent reads |
| `click(selector)` | Click an element by CSS selector or accessibility label |
| `type(selector, text)` | Type into a field |
| `scroll(direction, amount)` | Scroll the page |
| `screenshot()` | Capture PNG bytes — sent to vision-capable LLMs |
| `evaluate(js)` | Run arbitrary JavaScript and return the result |
| `close()` | Release the browser session |

Built-in adapters: `LocalPlaywrightAdapter` (headless Chromium, no cost),
`BrowserbaseAdapter` (cloud browser with CAPTCHA support and session persistence).

---

### WebSearchPort

**File:** `src/ravn/ports/web_search.py`
**Config key:** `tools.web_search.adapter`
**Cardinality:** exactly one (optional — `web_search` tool disabled if unset)

`WebSearchPort` backs the `web_search` tool. When the agent calls `web_search`,
this port runs the query against a real search index and returns a list of
`SearchResult` objects (title, URL, snippet) that the agent reads to decide which
pages to fetch. It is the difference between the agent having access to live web
information and being limited to its training data.

You would implement a custom `WebSearchPort` to use a specific search provider
(Brave, Bing, Google, DuckDuckGo, a self-hosted index), to restrict search to an
internal knowledge base or intranet, to add domain filtering (only return results
from trusted sources), or to implement caching for repeated queries.

| Abstract method | Signature |
|-----------------|-----------|
| `search` | `async (query: str, num_results: int) → list[SearchResult]` |

---

### MeshPort

**File:** `src/ravn/ports/mesh.py`
**Config key:** `mesh.adapter`
**Cardinality:** exactly one (optional — cascade and flock features disabled if unset)

`MeshPort` is the inter-node transport. When multiple Ravn instances form a flock
(a group of collaborating nodes), they communicate through the mesh: broadcasting
events on topics, subscribing to topics, and sending direct point-to-point
messages to specific peers. The cascade system uses the mesh to delegate tasks to
peer nodes and receive results back.

The interface abstracts over the physical transport — `NngMeshAdapter` uses nng
(nanomsg-next-generation) PUB/SUB and REQ/REP sockets directly between nodes,
which works well on a local network or Raspberry Pi cluster. `SleipnirMeshAdapter`
routes through RabbitMQ, which adds infrastructure overhead but gives you durable
queues, federation across data centres, and integration with the rest of the ODIN
backbone.

You would implement a custom `MeshPort` to use a different transport (NATS,
ZeroMQ, gRPC, a proprietary message bus), to add message signing or encryption
between peers, or to run Ravn nodes across a WAN where direct socket connections
are not feasible.

| Method | Notes |
|--------|-------|
| `publish(event, topic)` | Broadcast to all subscribers on a topic |
| `subscribe(topic, handler)` | Register a handler for messages on a topic |
| `send(target_peer_id, message, timeout_s)` | Send to a specific peer and await reply |
| `start()` / `stop()` | Lifecycle |

Built-in adapters: `NngMeshAdapter` (direct sockets, low latency, no infra),
`SleipnirMeshAdapter` (RabbitMQ, durable, federated).

---

### DiscoveryPort

**File:** `src/ravn/ports/discovery.py`
**Config key:** `discovery.adapter`
**Cardinality:** exactly one (optional — flock features disabled if unset)

`DiscoveryPort` handles how Ravn nodes find each other. Before two nodes can
exchange messages over the mesh, they need to know each other exists, verify
identity, and establish trust. Discovery handles announcing presence, scanning
for candidates, and performing a handshake that produces a trusted `RavnPeer`
record (including capabilities and SPIFFE identity for mTLS).

`MdnsDiscoveryAdapter` broadcasts presence over multicast DNS — zero configuration,
works on any local network, ideal for Raspberry Pi clusters and home lab
deployments. `SleipnirDiscoveryAdapter` uses the RabbitMQ backbone for
announcement and a SPIFFE-JWT handshake for trust, suited to production
Kubernetes deployments where mDNS is not available. `K8sDiscoveryAdapter` watches
Pod label selectors directly.

You would implement a custom `DiscoveryPort` to integrate with a service registry
(Consul, etcd), to use a custom PKI for trust establishment, or to discover nodes
from a control plane rather than peer-to-peer broadcast.

| Method | Notes |
|--------|-------|
| `start()` / `stop()` | Lifecycle |
| `announce()` | Broadcast this node's presence and capabilities |
| `scan()` | Find peer candidates on the network |
| `watch(on_join, on_leave)` | Receive callbacks when peers join or leave |
| `handshake(candidate)` | Verify a candidate and produce a trusted `RavnPeer` |
| `peers()` | Synchronous snapshot of currently known peers |

Built-in adapters: `MdnsDiscoveryAdapter`, `SleipnirDiscoveryAdapter`, `K8sDiscoveryAdapter`.

---

### SpawnPort

**File:** `src/ravn/ports/spawn.py`
**Config key:** `cascade.spawn_adapter`
**Cardinality:** exactly one (optional — ephemeral spawn disabled if unset)

`SpawnPort` creates ephemeral Ravn instances on demand. When a task is too large
for a single agent, the cascade system can spawn worker agents, delegate
sub-tasks to them over the mesh, and collect their results — a map-reduce pattern
for agent work. The spawn port abstracts over how those workers are created:
as local subprocesses (fast, no infrastructure), or as Kubernetes Jobs (isolated,
scalable, cleaned up automatically on completion).

You would implement a custom `SpawnPort` to run ephemeral agents on a different
substrate (AWS Fargate, Google Cloud Run, a Slurm cluster), to pre-warm a pool
of agents rather than cold-starting them, or to enforce resource quotas per task.

| Method | Signature |
|--------|-----------|
| `spawn` | `async (count, config: SpawnConfig) → list[str]` — returns peer IDs |
| `terminate` | `async (peer_id) → None` |
| `terminate_all` | `async () → None` |

Built-in adapters: `SubprocessSpawnAdapter` (local processes, zero infrastructure),
`KubernetesJobSpawnAdapter` (isolated Jobs, auto-cleaned).

---

### SkillPort

**File:** `src/ravn/ports/skill.py`
**Config key:** `skill.backend`
**Cardinality:** exactly one (optional — skill tools disabled if unset)

`SkillPort` stores and retrieves reusable skills. A skill is a named, curated
procedure — a short markdown document describing how to accomplish a recurring
task (e.g. "how to deploy to staging", "how to write a Linear issue"). When a
skill is relevant to the current task, the agent can load it with the `skill_run`
tool and follow the procedure without needing to rediscover the steps from scratch.

Skills can be created manually (markdown files in `.ravn/skills/`) or extracted
automatically from completed episodes by `record_episode` — if the memory adapter
detects a successful, reusable procedure in a completed turn, it can call
`record_skill` to preserve it.

You would implement a custom `SkillPort` to store skills in a shared team
database (so skills discovered by one node are available to all), to version skills
(track when a procedure was last updated), or to integrate with an existing
runbook or playbook system.

| Abstract method | Signature |
|-----------------|-----------|
| `record_episode` | `async (episode) → Skill \| None` — extract a skill if the episode warrants it |
| `list_skills` | `async (query) → list[Skill]` |
| `record_skill` | `async (skill) → None` |

Optional override: `get_skill(name)`.

Built-in adapters: `SqliteSkillAdapter`, `FileSkillRegistry` (markdown files in `.ravn/skills/`).

---

### PreToolHookPort / PostToolHookPort / HookPipelinePort

**File:** `src/ravn/ports/hooks.py`
**Config key:** `hooks.pre[].adapter`, `hooks.post[].adapter`
**Cardinality:** any number — hooks run as an ordered pipeline

Hooks intercept every tool call at two points: before execution (pre-hooks) and
after execution (post-hooks). Pre-hooks receive the tool name and arguments and
can modify the arguments, block the call entirely by raising `PermissionDeniedError`,
or add audit log entries before anything runs. Post-hooks receive the tool result
and can modify it, redact sensitive content, or append metadata.

This is the right place for cross-cutting concerns that apply to all tools without
any tool needing to know about them: permission enforcement, budget tracking,
audit logging, output sanitisation, PII redaction. Multiple hooks stack in order
— the output of one pre-hook becomes the input arguments for the next, and the
output of one post-hook becomes the input result for the next.

You would implement a custom hook to add compliance logging (record every tool
call with full arguments to an audit trail), to enforce data-residency rules (block
any write that would put data outside an approved region), to add rate limiting per
tool, or to post-process results (strip secrets from bash output, normalise file
paths, etc.).

**PreToolHookPort:**
```python
async def pre_execute(tool_name, args, agent_state) -> dict
# Return (potentially modified) args, or raise PermissionDeniedError to block
```

**PostToolHookPort:**
```python
async def post_execute(tool_name, args, result, agent_state) -> ToolResult
# Return (potentially modified) result
```

Built-in hooks: `PermissionHook` (checks `PermissionPort`), `EnforcerHook`
(checks `PermissionEnforcerPort`), `BudgetHook` (blocks when iteration budget
is exhausted), `AuditHook` (logs all tool calls), `SanitisationHook` (redacts
dangerous patterns from bash output).

---

## Engine Configuration

These are not ports (no ABC to implement) but are documented here because they
configure subsystems that work directly alongside the port/adapter layer.

### InitiativeConfig — Drive Loop

**Config key:** `initiative`
**File:** `src/ravn/config.py` → `InitiativeConfig`

The initiative engine is Ravn's autonomous task execution system. It runs
indefinitely as a daemon, draining a priority queue of `AgentTask` instances
that registered triggers fire into it.

```
Trigger (long-lived) → enqueues AgentTask → DriveLoop._task_executor → RavnAgent.run_turn
```

"Initiative" is the name of the engine, not a domain concept — a triggered task
is simply an `AgentTask`, indistinguishable from a user-submitted one except for
its `triggered_by` field and `output_mode`. The queue is persisted to disk so
pending tasks survive daemon restarts, and a file lock prevents concurrent daemon
instances from executing the same task twice.

The drive loop is only active in daemon mode (`ravn daemon`). Interactive
sessions (`ravn run`) never start it.

| Field | Default | Description |
|-------|---------|-------------|
| `enabled` | `false` | Enable the drive loop. Nothing runs unless this is `true`. |
| `max_concurrent_tasks` | `3` | Maximum simultaneously executing tasks. |
| `task_queue_max` | `50` | Maximum tasks held in the priority queue. |
| `queue_journal_path` | `~/.ravn/daemon/queue.json` | Persistence path — pending tasks survive restarts. |
| `default_output_mode` | `silent` | Output mode applied when a trigger doesn't specify one. |
| `default_persona` | `""` | Persona applied when a trigger doesn't specify one. |
| `heartbeat_interval_seconds` | `60` | How often the drive loop logs a heartbeat. |
| `cron_tick_seconds` | `30` | How often the cron scheduler wakes to check jobs. |
| `trigger_adapters` | `[]` | List of `TriggerAdapterConfig` — see [TriggerPort](#triggerport) above. |

**Example:**

```yaml
initiative:
  enabled: true
  max_concurrent_tasks: 5
  default_persona: autonomous-agent
  trigger_adapters:
    - adapter: ravn.adapters.triggers.cron.CronTrigger
      kwargs:
        jobs:
          - name: morning-review
            schedule: "0 8 * * 1-5"
            context: "Review overnight activity and summarise priorities."
            output_mode: ambient
    - adapter: mypackage.triggers.WebhookTrigger
      kwargs:
        port: 9000
```

---

## Adding a new port

When a new pluggable subsystem is needed:

1. **Define the ABC** in `src/ravn/ports/<name>.py` — abstract methods only, no infrastructure imports.
2. **Write the default adapter** in `src/ravn/adapters/<name>/`. The adapter inherits from the port and implements all abstract methods.
3. **Add a config model** in `src/ravn/config.py` with an `adapter: str` field (dotted class path) and optional `kwargs`/`secret_kwargs_env` fields.
4. **Add a `_build_<name>(settings)` factory** in `src/ravn/cli/commands.py` that calls `_import_class(settings.<name>.adapter)` and passes kwargs.
5. **Update this document.**
