import type {
  IRegistryRepository,
  ILiveTopologyStream,
  IEventStream,
  TopologyListener,
  ObservatoryEventListener,
} from '../ports';
import type { Registry, Topology, TopologyNode, TopologyEdge, ObservatoryEvent } from '../domain';

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
      canContain: ['service', 'raid', 'tyr', 'bifrost', 'volundr', 'valkyrie', 'mimir'],
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
      shape: 'rounded-rect',
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
      shape: 'diamond',
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
      id: 'ravn_raid',
      label: 'Raid Ravn',
      rune: 'ᚲ',
      icon: 'bird',
      shape: 'triangle',
      color: 'ice-300',
      size: 8,
      border: 'solid',
      canContain: [],
      parentTypes: ['raid'],
      category: 'agent',
      description: 'Ephemeral raven conscripted into a raid. Coord, Reviewer, or Scholar role.',
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
      parentTypes: ['raid', 'cluster'],
      category: 'agent',
      description: 'WebSocket broker — pair-bonded to a raid for chat fan-out.',
      fields: [],
    },
    {
      id: 'valkyrie',
      label: 'Valkyrie',
      rune: 'ᛒ',
      icon: 'shield',
      shape: 'chevron',
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
      id: 'tyr',
      label: 'Týr',
      rune: 'ᛃ',
      icon: 'git-branch',
      shape: 'square',
      color: 'brand',
      size: 16,
      border: 'solid',
      canContain: [],
      parentTypes: ['cluster', 'realm'],
      category: 'coordinator',
      description:
        'Saga / raid orchestrator. One per cluster; dispatches raids to coordinate work across Völundrs.',
      fields: [
        { key: 'activeSagas', label: 'Active sagas', type: 'number' },
        { key: 'pendingRaids', label: 'Pending raids', type: 'number' },
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
      shape: 'square',
      color: 'brand',
      size: 16,
      border: 'solid',
      canContain: [],
      parentTypes: ['cluster', 'realm'],
      category: 'coordinator',
      description:
        'Session forge — spawns and manages remote development pods. Directly connected to Týrs.',
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
      canContain: ['mimir_sub'],
      parentTypes: ['cluster', 'realm'],
      category: 'knowledge',
      description:
        'The well of knowledge. Primary indexer. All long-lived ravens read from and write to Mímir.',
      fields: [
        { key: 'pages', label: 'Pages', type: 'number' },
        { key: 'writes', label: 'Writes', type: 'number' },
      ],
    },
    {
      id: 'mimir_sub',
      label: 'Sub-Mímir',
      rune: 'ᛗ',
      icon: 'book-marked',
      shape: 'mimir-small',
      color: 'ice-200',
      size: 18,
      border: 'solid',
      canContain: [],
      parentTypes: ['mimir'],
      category: 'knowledge',
      description: 'Domain-scoped Mímir — code, ops, lore. Sits in orbit around the primary Mímir.',
      fields: [{ key: 'purpose', label: 'Purpose', type: 'string' }],
    },
    {
      id: 'service',
      label: 'Service',
      rune: 'ᛦ',
      icon: 'box',
      shape: 'dot',
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
      shape: 'dot',
      color: 'slate-300',
      size: 7,
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
      shape: 'dot',
      color: 'slate-400',
      size: 5,
      border: 'dashed',
      canContain: [],
      parentTypes: ['realm'],
      category: 'device',
      description: 'ESPresense BLE beacon — low-power wireless presence detection.',
      fields: [],
    },
    {
      id: 'raid',
      label: 'Raid',
      rune: 'ᚷ',
      icon: 'users',
      shape: 'halo',
      color: 'brand',
      size: 50,
      border: 'dashed',
      canContain: ['ravn_raid', 'skuld'],
      parentTypes: ['cluster'],
      category: 'composite',
      description:
        'Ephemeral flock — ravens dispatched by a Týr to execute a saga. Forms, works, dissolves.',
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

const SEED_NODES: TopologyNode[] = [
  {
    id: 'realm-asgard',
    typeId: 'realm',
    label: 'asgard',
    parentId: null,
    status: 'healthy',
    zone: 'asgard',
    vlan: 90,
    dns: 'asgard.niuu.world',
    purpose: 'AI / compute / dev',
    activity: 'idle',
  },
  {
    id: 'cluster-valaskjalf',
    typeId: 'cluster',
    label: 'valaskjálf',
    parentId: 'realm-asgard',
    status: 'healthy',
    zone: 'asgard',
    purpose: 'DGX Spark cluster',
    activity: 'idle',
  },
  {
    id: 'cluster-valhalla',
    typeId: 'cluster',
    label: 'valhalla',
    parentId: 'realm-asgard',
    status: 'healthy',
    zone: 'asgard',
    purpose: 'AI/ML workloads',
    activity: 'idle',
  },
  {
    id: 'host-mjolnir',
    typeId: 'host',
    label: 'mjölnir',
    parentId: 'realm-asgard',
    status: 'healthy',
    zone: 'asgard',
    hw: 'DGX Spark',
    os: 'Ubuntu 24',
    cores: 144,
    ram: '1 TiB',
    gpu: 'GH200',
    activity: 'idle',
  },
  {
    id: 'tyr-0',
    typeId: 'tyr',
    label: 'tyr-0',
    parentId: 'cluster-valaskjalf',
    status: 'healthy',
    zone: 'asgard',
    cluster: 'valaskjalf',
    mode: 'active',
    activeSagas: 3,
    pendingRaids: 2,
    activity: 'thinking',
  },
  {
    id: 'bifrost-0',
    typeId: 'bifrost',
    label: 'bifröst-0',
    parentId: 'cluster-valaskjalf',
    status: 'healthy',
    zone: 'asgard',
    cluster: 'valaskjalf',
    providers: ['Anthropic', 'OpenAI', 'Google', 'Local'],
    reqPerMin: 42,
    cacheHitRate: 0.68,
    activity: 'idle',
  },
  {
    id: 'volundr-0',
    typeId: 'volundr',
    label: 'völundr-0',
    parentId: 'cluster-valhalla',
    status: 'healthy',
    zone: 'asgard',
    cluster: 'valhalla',
    activeSessions: 5,
    maxSessions: 20,
    activity: 'tooling',
  },
  {
    id: 'mimir-0',
    typeId: 'mimir',
    label: 'mímir-0',
    parentId: 'cluster-valaskjalf',
    status: 'healthy',
    zone: 'asgard',
    cluster: 'valaskjalf',
    activity: 'reading',
  },
  {
    id: 'raid-0',
    typeId: 'raid',
    label: 'raid-omega',
    parentId: 'cluster-valaskjalf',
    status: 'observing',
    zone: 'asgard',
    cluster: 'valaskjalf',
    purpose: 'refactor bifrost rule engine',
    state: 'working',
    activity: 'delegating',
  },
  {
    id: 'ravn-huginn',
    typeId: 'ravn_long',
    label: 'huginn',
    parentId: 'host-mjolnir',
    status: 'healthy',
    zone: 'asgard',
    hostId: 'host-mjolnir',
    persona: 'thought',
    specialty: 'architecture & design',
    tokens: 42800,
    activity: 'thinking',
  },
  {
    id: 'ravn-muninn',
    typeId: 'ravn_long',
    label: 'muninn',
    parentId: 'host-mjolnir',
    status: 'idle',
    zone: 'asgard',
    hostId: 'host-mjolnir',
    persona: 'memory',
    specialty: 'history & context',
    tokens: 18200,
    activity: 'idle',
  },
];

const SEED_EDGES: TopologyEdge[] = [
  // solid: direct coordinator link
  { id: 'e-tyr-volundr', sourceId: 'tyr-0', targetId: 'volundr-0', kind: 'solid' },
  // dashed-anim: active raid dispatch
  { id: 'e-tyr-raid', sourceId: 'tyr-0', targetId: 'raid-0', kind: 'dashed-anim' },
  // dashed-long: raven async memory access
  { id: 'e-huginn-mimir', sourceId: 'ravn-huginn', targetId: 'mimir-0', kind: 'dashed-long' },
  // soft: bifrost references mimir for cache
  { id: 'e-bifrost-mimir', sourceId: 'bifrost-0', targetId: 'mimir-0', kind: 'soft' },
  // raid: inter-raven coordination within the raid
  { id: 'e-raid-huginn', sourceId: 'raid-0', targetId: 'ravn-huginn', kind: 'raid' },
];

const SEED_TOPOLOGY: Topology = {
  nodes: SEED_NODES,
  edges: SEED_EDGES,
  timestamp: '2026-04-19T00:00:00Z',
};

// ── Seed events (web2 format: time, type, subject, body) ─────────────────────

const SEED_EVENTS: ObservatoryEvent[] = [
  {
    id: 'ev-1',
    time: '00:00:01',
    type: 'RAID',
    subject: 'raid-omega',
    body: 'tyr dispatched raid · "refactor bifrost rule engine"',
  },
  {
    id: 'ev-2',
    time: '00:00:05',
    type: 'RAVN',
    subject: 'huginn',
    body: 'huginn joined raid-omega as coord',
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
  return {
    async getRegistry(): Promise<Registry> {
      await new Promise<void>((r) => setTimeout(r, 50));
      return SEED_REGISTRY;
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
