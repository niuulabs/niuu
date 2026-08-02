import type {
  IAgentDirectory,
  IRegistryRepository,
  ILiveTopologyStream,
  IEventStream,
  TopologyListener,
  ObservatoryEventListener,
} from '../ports';
import { SEED_NODES, SEED_TOPOLOGY } from './seedTopology';
import type {
  AgentDirectoryEntry,
  AgentDirectoryFilters,
  AgentDirectoryPage,
  AgentKind,
  Registry,
  Topology,
  TopologyNode,
  ObservatoryEvent,
} from '../domain';

// ── Seed registry (mirrors the earlier prototype DEFAULT_REGISTRY seed data) ──

const SEED_REGISTRY: Registry = {
  version: 7,
  updatedAt: '2026-04-15T09:24:11Z',
  types: [
    {
      id: 'realm',
      label: 'Realm',
      rune: 'ᛞ',
      icon: 'globe',
      shape: 'ring',
      color: 'ice-100',
      size: 18,
      border: 'solid',
      canContain: ['cluster', 'host', 'ravn_long', 'valkyrie', 'printer', 'vaettir', 'beacon'],
      parentTypes: [],
      category: 'topology',
      description:
        'VLAN-scoped network zone — asgard, midgard, svartalfheim, etc. Every entity lives in exactly one realm.',
      fields: [
        { key: 'vlan', label: 'VLAN', type: 'number', required: true },
        { key: 'dns', label: 'DNS zone', type: 'string', required: true },
        { key: 'purpose', label: 'Purpose', type: 'string' },
      ],
    },
    {
      id: 'cluster',
      label: 'Cluster',
      rune: 'ᚲ',
      icon: 'layers',
      shape: 'ring-dashed',
      color: 'ice-200',
      size: 14,
      border: 'dashed',
      canContain: ['service', 'run', 'ting', 'bifrost', 'volundr', 'valkyrie', 'mimir'],
      parentTypes: ['realm'],
      category: 'topology',
      description:
        'Kubernetes cluster nested inside a realm. Valaskjálf, Valhalla, Nóatún, Eitri, Glitnir, Járnviðr.',
      fields: [
        { key: 'purpose', label: 'Purpose', type: 'string' },
        { key: 'nodes', label: 'Nodes', type: 'number' },
      ],
    },
    {
      id: 'host',
      label: 'Host',
      rune: 'ᚦ',
      icon: 'server',
      shape: 'rack',
      color: 'slate-400',
      size: 22,
      border: 'solid',
      canContain: ['ravn_long', 'service'],
      parentTypes: ['realm'],
      category: 'hardware',
      description: 'Bare-metal or VM. DGX Sparks, Mac minis, EPYC boxes, user laptops.',
      fields: [
        { key: 'hw', label: 'Hardware', type: 'string' },
        { key: 'os', label: 'OS', type: 'string' },
        { key: 'cores', label: 'Cores', type: 'number' },
        { key: 'ram', label: 'RAM', type: 'string' },
        { key: 'gpu', label: 'GPU', type: 'string' },
      ],
    },
    {
      id: 'ravn_long',
      label: 'Long-lived Ravn',
      rune: 'ᚱ',
      icon: 'bird',
      shape: 'agent',
      color: 'brand',
      size: 11,
      border: 'solid',
      canContain: [],
      parentTypes: ['host', 'cluster', 'realm'],
      category: 'agent',
      description:
        'Persistent raven agent bound to a host or free-orbiting around Mímir. Persona, specialty, tool access.',
      fields: [
        {
          key: 'persona',
          label: 'Persona',
          type: 'select',
          options: ['thought', 'memory', 'strength', 'battle', 'noise', 'valkyrie'],
        },
        { key: 'specialty', label: 'Specialty', type: 'string' },
        { key: 'tokens', label: 'Tokens', type: 'number' },
      ],
    },
    {
      id: 'ravn_run',
      label: 'Run Ravn',
      rune: 'ᚲ',
      icon: 'bird',
      shape: 'triangle',
      color: 'ice-300',
      size: 8,
      border: 'solid',
      canContain: [],
      parentTypes: ['run'],
      category: 'agent',
      description: 'Ephemeral raven conscripted into a run. Coord, Reviewer, or Scholar role.',
      fields: [
        { key: 'role', label: 'Role', type: 'select', options: ['coord', 'reviewer', 'scholar'] },
        { key: 'confidence', label: 'Confidence', type: 'number' },
      ],
    },
    {
      id: 'skuld',
      label: 'Skuld',
      rune: 'ᛜ',
      icon: 'radio',
      shape: 'hex',
      color: 'ice-200',
      size: 9,
      border: 'solid',
      canContain: [],
      parentTypes: ['run', 'cluster'],
      category: 'agent',
      description: 'WebSocket broker — pair-bonded to a run for chat fan-out.',
      fields: [],
    },
    {
      id: 'valkyrie',
      label: 'Valkyrie',
      rune: 'ᛒ',
      icon: 'shield',
      shape: 'agent',
      color: 'brand-400',
      size: 13,
      border: 'solid',
      canContain: [],
      parentTypes: ['cluster', 'realm'],
      category: 'agent',
      description:
        'Autonomous guardian agent. Takes action at the cluster level — restarts, failovers, scale events.',
      fields: [
        { key: 'specialty', label: 'Specialty', type: 'string' },
        {
          key: 'autonomy',
          label: 'Autonomy',
          type: 'select',
          options: ['full', 'notify', 'restricted'],
        },
      ],
    },
    {
      id: 'ting',
      label: 'Ting',
      rune: '✦',
      icon: 'git-branch',
      shape: 'box',
      color: 'brand',
      size: 16,
      border: 'solid',
      canContain: [],
      parentTypes: ['cluster', 'realm'],
      category: 'coordinator',
      description:
        'Saga / run orchestrator. One per cluster; dispatches runs to coordinate work across Völundrs.',
      fields: [
        { key: 'activeSagas', label: 'Active sagas', type: 'number' },
        { key: 'pendingRuns', label: 'Pending runs', type: 'number' },
        { key: 'mode', label: 'Mode', type: 'select', options: ['active', 'standby'] },
      ],
    },
    {
      id: 'bifrost',
      label: 'Bifröst',
      rune: 'ᚨ',
      icon: 'waves',
      shape: 'pentagon',
      color: 'brand',
      size: 15,
      border: 'solid',
      canContain: ['model'],
      parentTypes: ['cluster', 'realm'],
      category: 'coordinator',
      description:
        'LLM gateway. Routes inference to providers — Anthropic, OpenAI, Google, local Ollama, local vLLM.',
      fields: [
        { key: 'reqPerMin', label: 'Req/min', type: 'number' },
        { key: 'cacheHitRate', label: 'Cache hit %', type: 'number' },
        { key: 'providers', label: 'Providers', type: 'tags' },
      ],
    },
    {
      id: 'volundr',
      label: 'Völundr',
      rune: 'ᚲ',
      icon: 'hammer',
      shape: 'box',
      color: 'brand',
      size: 16,
      border: 'solid',
      canContain: [],
      parentTypes: ['cluster', 'realm'],
      category: 'coordinator',
      description:
        'Session forge — spawns and manages remote development pods. Directly connected to Tings.',
      fields: [
        { key: 'activeSessions', label: 'Active', type: 'number' },
        { key: 'maxSessions', label: 'Max', type: 'number' },
      ],
    },
    {
      id: 'mimir',
      label: 'Mímir',
      rune: 'ᛗ',
      icon: 'book-open',
      shape: 'mimir',
      color: 'ice-100',
      size: 42,
      border: 'solid',
      canContain: [],
      parentTypes: ['cluster', 'realm'],
      category: 'knowledge',
      description:
        'The well of knowledge. Primary indexer. All long-lived ravens read from and write to Mímir.',
      fields: [
        { key: 'pages', label: 'Pages', type: 'number' },
        { key: 'writes', label: 'Writes', type: 'number' },
        { key: 'mountCount', label: 'Mounts', type: 'number' },
      ],
    },
    {
      id: 'service',
      label: 'Service',
      rune: 'ᛦ',
      icon: 'box',
      shape: 'box',
      color: 'ice-300',
      size: 8,
      border: 'solid',
      canContain: [],
      parentTypes: ['cluster', 'host'],
      category: 'infrastructure',
      description:
        'Kubernetes workload — Sleipnir, Keycloak, OpenBao, Cerbos, Harbor, Grafana, vLLM, Ollama, etc.',
      fields: [
        {
          key: 'svcType',
          label: 'Type',
          type: 'select',
          options: [
            'rabbitmq',
            'auth',
            'secrets',
            'authz',
            'database',
            'inference',
            'registry',
            'gitops',
            'dashboard',
            'logs',
            'traces',
            'media',
            'manufacturing',
            'orchestrator',
          ],
        },
      ],
    },
    {
      id: 'model',
      label: 'LLM Model',
      rune: 'ᛖ',
      icon: 'cpu',
      shape: 'hex-flat',
      color: 'slate-300',
      size: 13,
      border: 'solid',
      canContain: [],
      parentTypes: ['bifrost', 'realm'],
      category: 'knowledge',
      description:
        'Inference endpoint behind Bifröst. External (Anthropic, OpenAI, Google) drawn as long threads; internal (vLLM, Ollama) short.',
      fields: [
        { key: 'provider', label: 'Provider', type: 'string' },
        { key: 'location', label: 'Location', type: 'select', options: ['internal', 'external'] },
      ],
    },
    {
      id: 'printer',
      label: 'Resin Printer',
      rune: 'ᛈ',
      icon: 'printer',
      shape: 'square-sm',
      color: 'slate-400',
      size: 10,
      border: 'solid',
      canContain: [],
      parentTypes: ['realm'],
      category: 'device',
      description:
        'SLA resin printer on YDP WebSocket. Saturn 4 Ultras named after legendary weapons.',
      fields: [{ key: 'model', label: 'Model', type: 'string' }],
    },
    {
      id: 'vaettir',
      label: 'Vættir Room Node',
      rune: 'ᚹ',
      icon: 'mic',
      shape: 'square-sm',
      color: 'slate-400',
      size: 9,
      border: 'solid',
      canContain: [],
      parentTypes: ['realm'],
      category: 'device',
      description:
        'ESP32 room presence node — mmWave, mic, speaker. Named for the locale it inhabits.',
      fields: [{ key: 'sensors', label: 'Sensors', type: 'tags' }],
    },
    {
      id: 'beacon',
      label: 'Presence Beacon',
      rune: 'ᚠ',
      icon: 'wifi',
      shape: 'beacon',
      color: 'slate-400',
      size: 9,
      border: 'dashed',
      canContain: [],
      parentTypes: ['realm'],
      category: 'device',
      description: 'ESPresense BLE beacon — low-power wireless presence detection.',
      fields: [],
    },
    {
      id: 'run',
      label: 'Run',
      rune: 'ᚷ',
      icon: 'users',
      shape: 'halo',
      color: 'brand',
      size: 50,
      border: 'dashed',
      canContain: ['ravn_run', 'skuld'],
      parentTypes: ['cluster'],
      category: 'composite',
      description:
        'Ephemeral flock — ravns dispatched by Ting to execute a saga. Forms, works, dissolves.',
      fields: [
        { key: 'purpose', label: 'Purpose', type: 'string' },
        {
          key: 'state',
          label: 'State',
          type: 'select',
          options: ['forming', 'working', 'dissolving'],
        },
        { key: 'composition', label: 'Composition', type: 'tags' },
      ],
    },
  ],
};

// ── Seed topology ─────────────────────────────────────────────────────────────

const SEED_EVENTS: ObservatoryEvent[] = [
  {
    id: 'ev-1',
    time: '00:00:01',
    type: 'RUN',
    subject: 'run-omega',
    body: 'ting dispatched run · "refactor bifrost rule engine"',
  },
  {
    id: 'ev-2',
    time: '00:00:05',
    type: 'RAVN',
    subject: 'huginn',
    body: 'huginn joined run-omega as coord',
  },
  {
    id: 'ev-3',
    time: '00:00:12',
    type: 'BIFROST',
    subject: 'bifröst-0',
    body: 'cache hit rate 94% over last 60s',
  },
  {
    id: 'ev-4',
    time: '00:00:30',
    type: 'MIMIR',
    subject: 'mímir-0',
    body: 'write queue depth 412 — nearing threshold',
  },
  {
    id: 'ev-5',
    time: '00:01:00',
    type: 'RAVN',
    subject: 'muninn',
    body: 'muninn entering idle — no active sagas',
  },
];

// ── Factory functions ─────────────────────────────────────────────────────────

export function createMockRegistryRepository(): IRegistryRepository {
  let current = structuredClone(SEED_REGISTRY);
  return {
    async getRegistry(): Promise<Registry> {
      await new Promise<void>((r) => setTimeout(r, 50));
      return structuredClone(current);
    },
    async saveRegistry(registry: Registry): Promise<Registry> {
      current = structuredClone(registry);
      return structuredClone(current);
    },
  };
}

export function createMockTopologyStream(): ILiveTopologyStream {
  const listeners = new Set<TopologyListener>();

  return {
    getSnapshot(): Topology {
      return SEED_TOPOLOGY;
    },
    subscribe(listener: TopologyListener): () => void {
      listeners.add(listener);
      listener(SEED_TOPOLOGY);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

export function createMockEventStream(): IEventStream {
  return {
    subscribe(listener: ObservatoryEventListener): () => void {
      for (const event of SEED_EVENTS) {
        listener(event);
      }
      return () => {
        // mock: events already emitted synchronously; no interval to clear
      };
    },
  };
}

// ── Agent directory (A2A) ─────────────────────────────────────────────────────

/** Which directory kind a topology node projects as. */
function agentKindFor(node: TopologyNode): AgentKind | null {
  if (node.typeId === 'ravn_long') return 'resident';
  if (node.typeId === 'valkyrie') return 'steward';
  if (node.typeId === 'run') return 'workflow-session';
  return null;
}

const SKILLS_BY_NODE: Record<string, string[]> = {
  'ravn-huginn': ['gpu_pressure_probe', 'replica_warm', 'canary_triage'],
  'ravn-muninn': ['helm_release_diff', 'cert_expiry_sweep', 'recall_context'],
  'ravn-kvasir': ['page_compact', 'fact_promote', 'dedupe_entities'],
  'ravn-njord': ['rollout_drain', 'pg_failover_probe', 'schema_diff'],
  'ravn-forseti': ['trace_correlate', 'ingester_lag_watch', 'series_budget'],
  'ravn-angrboda': ['transcode_queue', 'library_scan_window'],
  'ravn-freyja': ['vm_rebalance', 'migration_window', 'host_pressure'],
  'ravn-ivaldi': ['spindle_load_watch', 'nats_gap_detect', 'shift_handover'],
  'ravn-eldhrimnir': ['direct_infer', 'model_warm', 'thermal_guard'],
  'ravn-vidar': ['host_signature', 'evict_predict'],
  'run-research': ['literature_sweep', 'source_triage', 'compose_brief'],
  'run-coding': ['refactor_plan', 'apply_patch', 'verify_build'],
};

/**
 * Project the agent-bearing seed nodes into directory entries.
 *
 * The card fields mirror the shape a real A2A agent card carries so the UI can
 * be built against the same contract the HTTP adapter returns.
 */
function seedAgents(): AgentDirectoryEntry[] {
  return SEED_NODES.flatMap((node) => {
    const kind = agentKindFor(node);
    if (!kind) return [];

    const clusterId = node.cluster ?? node.zone ?? 'valaskjalf';
    const host = `${node.label}.${clusterId}.asgard.niuu.world`;
    const skillIds = SKILLS_BY_NODE[node.id] ?? [];

    return [
      {
        id: `agent-${node.id}`,
        canonicalId: `niuu:agent:${node.id}`,
        sourceAgentId: node.id,
        sourceInstanceId: `observatory-${clusterId}`,
        clusterId,
        environmentId: null,
        topologyNodeId: node.id,
        name: node.label,
        description:
          node.purpose ?? node.specialty ?? `${kind} projected from topology node ${node.id}`,
        kind,
        cardUrl: `https://${host}/.well-known/agent-card.json`,
        cardVersion: '0.0.1',
        cardHash: `sha256:${node.id}`,
        signatureVerified: kind === 'workflow-session' ? null : true,
        signatureKeyIds: kind === 'workflow-session' ? [] : ['niuu-a2a-signing'],
        signatureKeyFingerprints: kind === 'workflow-session' ? [] : ['SHA256:mock-fingerprint'],
        skillIds,
        tags: [kind, clusterId],
        defaultInputModes: ['text/plain', 'application/json'],
        defaultOutputModes: ['text/plain', 'application/json'],
        supportedInterfaces: [
          {
            url: `https://${host}/a2a`,
            protocolBinding: 'JSONRPC',
            protocolVersion: '0.3.0',
            tenant: 'niuu.world',
          },
        ],
        capabilities: {
          streaming: true,
          pushNotifications: kind !== 'workflow-session',
          stateTransitionHistory: true,
        },
        securitySchemes: { oauth2: { type: 'oauth2', flows: { clientCredentials: {} } } },
        securityRequirements: [{ oauth2: [] }],
        observedStatus: node.status,
        activity: node.activity ?? 'idle',
        lastSeen: SEED_TOPOLOGY.timestamp,
        ownerId: null,
        tenantId: 'niuu.world',
        visibility: 'realm',
        provenance: [
          {
            sourceAgentId: node.id,
            sourceInstanceId: `observatory-${clusterId}`,
            clusterId,
            environmentId: null,
            topologyNodeId: node.id,
          },
        ],
      },
    ];
  });
}

const SEED_AGENTS: AgentDirectoryEntry[] = seedAgents();

/** Every filter is AND-ed; each is satisfied when the entry matches any value. */
function matchesFilters(entry: AgentDirectoryEntry, filters: AgentDirectoryFilters): boolean {
  const anyOf = (values: readonly string[] | undefined, has: (value: string) => boolean) =>
    !values || values.length === 0 || values.some(has);

  return (
    anyOf(filters.skills, (skill) => entry.skillIds.includes(skill)) &&
    anyOf(filters.tags, (tag) => entry.tags.includes(tag)) &&
    anyOf(filters.kinds, (kind) => entry.kind === kind) &&
    anyOf(filters.statuses, (status) => entry.observedStatus === status) &&
    anyOf(filters.clusterIds, (clusterId) => entry.clusterId === clusterId) &&
    anyOf(filters.instanceIds, (instanceId) => entry.sourceInstanceId === instanceId) &&
    anyOf(filters.environmentIds, (envId) => entry.environmentId === envId)
  );
}

export function createMockAgentDirectory(): IAgentDirectory {
  return {
    async listAgents(filters: AgentDirectoryFilters = {}): Promise<AgentDirectoryPage> {
      const items = SEED_AGENTS.filter((entry) => matchesFilters(entry, filters));
      return structuredClone({
        items,
        warnings: [],
        sources: [
          {
            instanceId: 'observatory-valaskjalf',
            clusterId: 'valaskjalf',
            status: 'healthy' as const,
            revision: SEED_TOPOLOGY.timestamp,
            message: '',
          },
        ],
        partial: false,
        revision: SEED_TOPOLOGY.timestamp,
      });
    },

    async getAgent(agentId: string): Promise<AgentDirectoryEntry> {
      const entry = SEED_AGENTS.find((a) => a.id === agentId || a.sourceAgentId === agentId);
      // Fail loudly: a missing agent is a wiring bug, not an empty result.
      if (!entry) throw new Error(`Unknown agent: ${agentId}`);
      return structuredClone(entry);
    },
  };
}
