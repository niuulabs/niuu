/**
 * Seed topology for demo mode.
 *
 * Shaped after the real platform: five DNS realms, eight clusters, the DGX
 * Spark fleet, one resident per cluster plus two on bare metal, two live
 * workflow sessions, and the several distinct Mímir instances. Counts are the
 * ones the clusters actually report.
 *
 * Containment follows the registry's `parentTypes`: hosts and devices hang off
 * a realm, services and runs off a cluster, run agents off their run, models
 * off a Bifröst.
 */

import type { Topology, TopologyEdge, TopologyNode } from '../domain';

const TIMESTAMP = '2026-08-01T12:00:00Z';

// ── Realms ────────────────────────────────────────────────────────────────────

const REALMS: TopologyNode[] = [
  {
    id: 'realm-asgard',
    typeId: 'realm',
    label: 'asgard',
    parentId: null,
    status: 'healthy',
    zone: 'asgard',
    vlan: 90,
    dns: 'asgard.niuu.world',
    purpose: 'platform, GPU and hypervisor',
  },
  {
    id: 'realm-yggdrasil',
    typeId: 'realm',
    label: 'yggdrasil',
    parentId: null,
    status: 'healthy',
    zone: 'yggdrasil',
    vlan: 10,
    dns: 'yggdrasil.niuu.world',
    purpose: 'platform and identity',
  },
  {
    id: 'realm-alfheim',
    typeId: 'realm',
    label: 'alfheim',
    parentId: null,
    status: 'healthy',
    zone: 'alfheim',
    vlan: 20,
    dns: 'alfheim.niuu.world',
    purpose: 'observability and object store',
  },
  {
    id: 'realm-svartalfheim',
    typeId: 'realm',
    label: 'svartalfheim',
    parentId: null,
    status: 'healthy',
    zone: 'svartalfheim',
    vlan: 30,
    dns: 'svartalfheim.niuu.world',
    purpose: 'workshop and OT',
  },
  {
    id: 'realm-midgard',
    typeId: 'realm',
    label: 'midgard',
    parentId: null,
    status: 'healthy',
    zone: 'midgard',
    vlan: 40,
    dns: 'midgard.niuu.world',
    purpose: 'general workloads',
  },
];

// ── Clusters ──────────────────────────────────────────────────────────────────

interface ClusterSeed {
  id: string;
  label: string;
  realm: string;
  zone: string;
  purpose: string;
  nodes: number;
  status: TopologyNode['status'];
}

const CLUSTER_SEEDS: ClusterSeed[] = [
  {
    id: 'cluster-valaskjalf',
    label: 'valaskjálf',
    realm: 'realm-asgard',
    zone: 'asgard',
    purpose: 'GPU · inference · sandboxes — 9 nodes, 4 GPU, 225 pods',
    nodes: 9,
    status: 'healthy',
  },
  {
    id: 'cluster-valhalla',
    label: 'valhalla',
    realm: 'realm-asgard',
    zone: 'asgard',
    purpose: 'platform — cluster API unreachable, declared from chart values',
    nodes: 6,
    status: 'unknown',
  },
  {
    id: 'cluster-vanaheim',
    label: 'vanaheim',
    realm: 'realm-asgard',
    zone: 'asgard',
    purpose: 'harvester hypervisor — 9 hosts, 695 pods',
    nodes: 9,
    status: 'healthy',
  },
  {
    id: 'cluster-noatun',
    label: 'nóatún',
    realm: 'realm-asgard',
    zone: 'asgard',
    purpose: 'second niuu deployment — 6 nodes, 163 pods',
    nodes: 6,
    status: 'healthy',
  },
  {
    id: 'cluster-ymir',
    label: 'ymir',
    realm: 'realm-yggdrasil',
    zone: 'yggdrasil',
    purpose: 'platform · identity · mímir — 8 nodes, 227 pods',
    nodes: 8,
    status: 'healthy',
  },
  {
    id: 'cluster-glitnir',
    label: 'glitnir',
    realm: 'realm-alfheim',
    zone: 'alfheim',
    purpose: 'observability · object store — 8 nodes, 216 pods',
    nodes: 8,
    status: 'healthy',
  },
  {
    id: 'cluster-eitri',
    label: 'eitri',
    realm: 'realm-svartalfheim',
    zone: 'svartalfheim',
    purpose: 'workshop · OT · printers — 12 nodes, 218 pods',
    nodes: 12,
    status: 'healthy',
  },
  {
    id: 'cluster-jarnvidr',
    label: 'járnviðr',
    realm: 'realm-midgard',
    zone: 'midgard',
    purpose: 'media · general workloads — 7 nodes, 1 GPU, 190 pods',
    nodes: 7,
    status: 'healthy',
  },
];

const CLUSTERS: TopologyNode[] = CLUSTER_SEEDS.map((c) => ({
  id: c.id,
  typeId: 'cluster',
  label: c.label,
  parentId: c.realm,
  status: c.status,
  zone: c.zone,
  purpose: c.purpose,
  activity: 'idle',
}));

// ── Hosts ─────────────────────────────────────────────────────────────────────

function host(
  id: string,
  label: string,
  realm: string,
  zone: string,
  hw: string,
  cores: number,
  ram: string,
  gpu: string | null,
): TopologyNode {
  return {
    id,
    typeId: 'host',
    label,
    parentId: realm,
    status: 'healthy',
    zone,
    hw,
    os: 'talos',
    cores,
    ram,
    gpu,
  };
}

const HOSTS: TopologyNode[] = [
  host('host-saehrimnir', 'sæhrímnir', 'realm-asgard', 'asgard', 'DGX Spark', 20, '122 GB', 'GB10'),
  host(
    'host-tanngnjost',
    'tanngnjóst',
    'realm-asgard',
    'asgard',
    'DGX Spark',
    20,
    '120 GB',
    'GB10',
  ),
  host(
    'host-tanngrisnir',
    'tanngrisnir',
    'realm-asgard',
    'asgard',
    'DGX Spark',
    20,
    '122 GB',
    'GB10',
  ),
  host('host-baldr', 'baldr', 'realm-asgard', 'asgard', 'bare metal', 112, '441 GB', null),
  host('host-frigg', 'frigg', 'realm-asgard', 'asgard', 'bare metal', 112, '504 GB', null),
  host('host-alviss', 'alviss', 'realm-svartalfheim', 'svartalfheim', 'arm64', 4, '8 GB', null),
  host('host-brokk', 'brokk', 'realm-svartalfheim', 'svartalfheim', 'arm64', 4, '8 GB', null),
];

// ── Services ──────────────────────────────────────────────────────────────────

function service(
  id: string,
  label: string,
  parentId: string,
  zone: string,
  svcType: string,
  purpose: string,
  status: TopologyNode['status'] = 'healthy',
): TopologyNode {
  return { id, typeId: 'service', label, parentId, status, zone, svcType, purpose };
}

const SERVICES: TopologyNode[] = [
  // valaskjálf — GPU serving and sandboxes
  service(
    'svc-inference-gateway',
    'inference-gateway',
    'cluster-valaskjalf',
    'asgard',
    'serving',
    'llm-d gateway · ns vllm · 1/1',
  ),
  service(
    'svc-vllm-router',
    'vllm-router',
    'cluster-valaskjalf',
    'asgard',
    'serving',
    'vLLM deployment router · 1/1',
  ),
  service(
    'svc-openshell',
    'openshell',
    'cluster-valaskjalf',
    'asgard',
    'sandbox',
    'sandbox runtime · sts 1/1',
  ),
  service(
    'svc-openclaw',
    'openclaw',
    'cluster-valaskjalf',
    'asgard',
    'runtime',
    'agent runtime · sts 0/0 · scaled to zero',
    'idle',
  ),
  service('svc-nats-valaskjalf', 'nats', 'cluster-valaskjalf', 'asgard', 'mesh', 'nats · 1/1'),

  // ymir — platform and identity
  service('svc-guild', 'guild', 'cluster-ymir', 'yggdrasil', 'niuu', 'niuu-guild · 1/1'),
  service(
    'svc-keycloak',
    'keycloak',
    'cluster-ymir',
    'yggdrasil',
    'identity',
    'keycloak.niuu.world',
  ),
  service('svc-openbao', 'openbao', 'cluster-ymir', 'yggdrasil', 'secrets', 'openbao.niuu.world'),
  service(
    'svc-warden-ymir',
    'warden-agent',
    'cluster-ymir',
    'yggdrasil',
    'niuu',
    'mimir-shared-warden-agent · 1/1',
  ),
  service('svc-nats-ymir', 'nats', 'cluster-ymir', 'yggdrasil', 'mesh', 'nats-yggdrasil · 3/3'),

  // nóatún — second deployment
  service(
    'svc-envoy-noatun',
    'envoy-gateway',
    'cluster-noatun',
    'asgard',
    'gateway',
    'envoy-volundr-gateway · 1/1',
  ),
  service('svc-nats-noatun', 'nats', 'cluster-noatun', 'asgard', 'mesh', 'nats-noatun · 1/1'),

  // glitnir — observability. Grafana Mimir is a metrics TSDB, deliberately not
  // modelled as a `mimir` node: it shares the name but stores no agent memory.
  service(
    'svc-mimir-metrics',
    'mimir · metrics',
    'cluster-glitnir',
    'alfheim',
    'observability',
    'Grafana Mimir TSDB — 12 workloads, 3 ingesters. Not a knowledge base.',
  ),
  service('svc-grafana', 'grafana', 'cluster-glitnir', 'alfheim', 'observability', 'dashboards'),
  service('svc-loki', 'loki', 'cluster-glitnir', 'alfheim', 'observability', 'logs'),
  service('svc-tempo', 'tempo', 'cluster-glitnir', 'alfheim', 'observability', 'traces'),
  service('svc-minio', 'minio', 'cluster-glitnir', 'alfheim', 'storage', 'object store'),
  service('svc-nats-glitnir', 'nats', 'cluster-glitnir', 'alfheim', 'mesh', 'nats-glitnir · 1/1'),

  // eitri — workshop
  service(
    'svc-spoolman',
    'spoolman',
    'cluster-eitri',
    'svartalfheim',
    'workshop',
    'filament tracking',
  ),
  service(
    'svc-orca',
    'orca-slicer',
    'cluster-eitri',
    'svartalfheim',
    'workshop',
    'orca-slicer-api',
  ),
  service('svc-nats-eitri', 'nats', 'cluster-eitri', 'svartalfheim', 'mesh', 'nats-eitri · 1/1'),

  // vanaheim — hypervisor
  service(
    'svc-harvester',
    'harvester',
    'cluster-vanaheim',
    'asgard',
    'hypervisor',
    'hosts the guest clusters',
  ),
  service(
    'svc-rancher',
    'rancher',
    'cluster-vanaheim',
    'asgard',
    'management',
    'rancher.niuu.world',
  ),

  // járnviðr — media
  service('svc-plex', 'plex', 'cluster-jarnvidr', 'midgard', 'media', 'plex'),
  service(
    'svc-arr',
    '*arr stack',
    'cluster-jarnvidr',
    'midgard',
    'media',
    'radarr · sonarr · sabnzbd',
  ),
  service(
    'svc-nats-jarnvidr',
    'nats',
    'cluster-jarnvidr',
    'midgard',
    'mesh',
    'nats-jarnvidr · 1/1',
  ),
];

// ── Platform coordinators ─────────────────────────────────────────────────────

const COORDINATORS: TopologyNode[] = [
  {
    id: 'volundr-ymir',
    typeId: 'volundr',
    label: 'völundr',
    parentId: 'cluster-ymir',
    status: 'healthy',
    zone: 'yggdrasil',
    activeSessions: 4,
    maxSessions: 24,
  },
  {
    id: 'ting-ymir',
    typeId: 'ting',
    label: 'ting',
    parentId: 'cluster-ymir',
    status: 'healthy',
    zone: 'yggdrasil',
    mode: 'active',
    activeSagas: 2,
    pendingRuns: 1,
  },
  {
    id: 'bifrost-ymir',
    typeId: 'bifrost',
    label: 'bifröst',
    parentId: 'cluster-ymir',
    status: 'healthy',
    zone: 'yggdrasil',
    providers: ['anthropic', 'openai', 'xai', 'valaskjalf'],
    reqPerMin: 184,
    cacheHitRate: 0.62,
  },
  {
    id: 'volundr-noatun',
    typeId: 'volundr',
    label: 'völundr',
    parentId: 'cluster-noatun',
    status: 'healthy',
    zone: 'asgard',
    activeSessions: 1,
    maxSessions: 12,
  },
  {
    id: 'bifrost-noatun',
    typeId: 'bifrost',
    label: 'bifröst',
    parentId: 'cluster-noatun',
    status: 'healthy',
    zone: 'asgard',
    providers: ['valaskjalf'],
    reqPerMin: 46,
    cacheHitRate: 0.55,
  },
];

// ── Mímir — the knowledge instances ───────────────────────────────────────────

const MIMIRS: TopologyNode[] = [
  {
    id: 'mimir-ymir',
    typeId: 'mimir',
    label: 'mímir-shared',
    parentId: 'cluster-ymir',
    status: 'healthy',
    zone: 'yggdrasil',
    purpose: 'primary shared knowledge base — mimir.yggdrasil.niuu.world',
    pages: 203,
    writes: 12,
    mountCount: 2,
    mounts: ['local', 'shared'],
    activity: 'writing',
  },
  {
    id: 'mimir-noatun',
    typeId: 'mimir',
    label: 'mímir-shared',
    parentId: 'cluster-noatun',
    status: 'healthy',
    zone: 'asgard',
    purpose: 'second deployment — separate database, not a replica of ymir',
    pages: 64,
    writes: 3,
    mountCount: 1,
    mounts: ['shared'],
    activity: 'reading',
  },
  {
    id: 'mimir-valhalla',
    typeId: 'mimir',
    label: 'mímir-shared',
    parentId: 'cluster-valhalla',
    status: 'unknown',
    zone: 'asgard',
    purpose: 'declared by chart values; cluster API returns 403 so unconfirmed',
    mountCount: 1,
    mounts: ['shared'],
    activity: 'idle',
  },
];

// ── Models ────────────────────────────────────────────────────────────────────

function model(
  id: string,
  label: string,
  parentId: string,
  zone: string,
  provider: string,
  location: string,
  status: TopologyNode['status'],
): TopologyNode {
  return { id, typeId: 'model', label, parentId, status, zone, provider, location };
}

/**
 * Vendor clouds, mirroring what `BifrostCatalogDiscoveryAdapter` emits.
 *
 * A hosted model is not in our cluster — the Bifröst that calls it is. One
 * cloud per vendor, with no cluster or realm of its own.
 */
const CLOUDS: TopologyNode[] = [
  { id: 'cloud-anthropic', typeId: 'cloud', label: 'Anthropic', parentId: null, status: 'healthy' },
  { id: 'cloud-openai', typeId: 'cloud', label: 'OpenAI', parentId: null, status: 'healthy' },
  { id: 'cloud-xai', typeId: 'cloud', label: 'xAI', parentId: null, status: 'healthy' },
];

const MODELS: TopologyNode[] = [
  // Self-hosted weights stay under the gateway, inside the cluster that runs
  // them. `location` is the field compute class is read from, so it carries
  // the vocabulary the adapter emits rather than prose.
  model(
    'model-nemotron',
    'nemotron-3-super',
    'bifrost-ymir',
    'yggdrasil',
    'valaskjalf',
    'internal',
    'healthy',
  ),
  model(
    'model-qwen36',
    'qwen3.6-coder',
    'bifrost-ymir',
    'yggdrasil',
    'valaskjalf',
    'internal',
    'idle',
  ),
  model(
    'model-claude',
    'claude-fable-5',
    'cloud-anthropic',
    '',
    'anthropic',
    'external',
    'healthy',
  ),
  model('model-gpt', 'gpt-5.6-sol', 'cloud-openai', '', 'openai', 'external', 'healthy'),
  model('model-grok', 'grok-build', 'cloud-xai', '', 'xai', 'external', 'idle'),
];

// ── Residents ─────────────────────────────────────────────────────────────────

function resident(
  id: string,
  label: string,
  parentId: string,
  zone: string,
  specialty: string,
  flockId: string,
  status: TopologyNode['status'],
  activity: TopologyNode['activity'],
  tokens: number,
): TopologyNode {
  return {
    id,
    typeId: 'ravn_long',
    label,
    parentId,
    status,
    zone,
    specialty,
    flockId,
    persona: 'steward',
    tokens,
    activity,
  };
}

const RESIDENTS: TopologyNode[] = [
  resident(
    'ravn-huginn',
    'huginn',
    'cluster-valaskjalf',
    'asgard',
    'inference steward',
    'forge-mesh',
    'healthy',
    'thinking',
    42800,
  ),
  resident(
    'ravn-muninn',
    'muninn',
    'cluster-valhalla',
    'asgard',
    'platform steward',
    'ops-mesh',
    'healthy',
    'tooling',
    18200,
  ),
  resident(
    'ravn-kvasir',
    'kvasir',
    'cluster-ymir',
    'yggdrasil',
    'memory steward',
    'ops-mesh',
    'healthy',
    'writing',
    31500,
  ),
  resident(
    'ravn-njord',
    'njörð',
    'cluster-noatun',
    'asgard',
    'deployment steward',
    'workshop-mesh',
    'healthy',
    'tooling',
    12400,
  ),
  resident(
    'ravn-forseti',
    'forseti',
    'cluster-glitnir',
    'alfheim',
    'observability steward',
    'ops-mesh',
    'healthy',
    'reading',
    22600,
  ),
  resident(
    'ravn-angrboda',
    'angrboða',
    'cluster-jarnvidr',
    'midgard',
    'media steward',
    'workshop-mesh',
    'idle',
    'idle',
    4100,
  ),
  resident(
    'ravn-freyja',
    'freyja',
    'cluster-vanaheim',
    'asgard',
    'fleet steward',
    'ops-mesh',
    'healthy',
    'thinking',
    15900,
  ),
  resident(
    'ravn-ivaldi',
    'ivaldi',
    'cluster-eitri',
    'svartalfheim',
    'workshop steward',
    'workshop-mesh',
    'healthy',
    'tooling',
    27300,
  ),
  // Two residents run straight on bare metal, outside Kubernetes entirely.
  resident(
    'ravn-eldhrimnir',
    'eldhrímnir',
    'host-saehrimnir',
    'asgard',
    'on-host inference · systemd',
    'forge-mesh',
    'healthy',
    'thinking',
    51200,
  ),
  resident(
    'ravn-vidar',
    'víðarr',
    'host-baldr',
    'asgard',
    'host signatures · systemd',
    'ops-mesh',
    'healthy',
    'reading',
    9800,
  ),
];

// ── Workflow sessions ─────────────────────────────────────────────────────────

function runAgent(
  id: string,
  label: string,
  runId: string,
  zone: string,
  role: string,
  confidence: number,
  activity: TopologyNode['activity'],
): TopologyNode {
  return {
    id,
    typeId: 'ravn_run',
    label,
    parentId: runId,
    status: 'healthy',
    zone,
    role,
    confidence,
    flockId: runId,
    activity,
  };
}

const SESSIONS: TopologyNode[] = [
  {
    id: 'run-research',
    typeId: 'run',
    label: 'research-session',
    parentId: 'cluster-valhalla',
    status: 'observing',
    zone: 'asgard',
    purpose: 'prior art for the bifröst rate limiter',
    state: 'working',
    flockId: 'run-research',
    activity: 'delegating',
  },
  runAgent('run-research-planner', 'planner', 'run-research', 'asgard', 'coord', 0.86, 'thinking'),
  runAgent('run-research-search', 'searcher', 'run-research', 'asgard', 'scholar', 0.74, 'tooling'),
  runAgent('run-research-read', 'reader', 'run-research', 'asgard', 'scholar', 0.79, 'reading'),
  runAgent('run-research-write', 'writer', 'run-research', 'asgard', 'reviewer', 0.68, 'waiting'),
  {
    id: 'run-coding',
    typeId: 'run',
    label: 'coding-session',
    parentId: 'cluster-noatun',
    status: 'observing',
    zone: 'asgard',
    purpose: 'refactor the bifröst rule engine',
    state: 'working',
    flockId: 'run-coding',
    activity: 'delegating',
  },
  runAgent('run-coding-architect', 'architect', 'run-coding', 'asgard', 'coord', 0.91, 'thinking'),
  runAgent('run-coding-impl', 'implementer', 'run-coding', 'asgard', 'scholar', 0.82, 'writing'),
  runAgent('run-coding-review', 'reviewer', 'run-coding', 'asgard', 'reviewer', 0.7, 'waiting'),
  runAgent('run-coding-test', 'tester', 'run-coding', 'asgard', 'scholar', 0.77, 'tooling'),
];

// ── Workshop devices ──────────────────────────────────────────────────────────

const DEVICES: TopologyNode[] = [
  {
    id: 'device-bambuddy',
    typeId: 'printer',
    label: 'bambuddy',
    parentId: 'realm-svartalfheim',
    status: 'healthy',
    zone: 'svartalfheim',
    model: 'Bambu X1C fleet',
  },
  {
    id: 'device-ovfarm',
    typeId: 'vaettir',
    label: 'ovfarm',
    parentId: 'realm-svartalfheim',
    status: 'healthy',
    zone: 'svartalfheim',
    sensors: 'equipment · ot-operators',
  },
  {
    id: 'device-laevateinn',
    typeId: 'beacon',
    label: 'lævateinn',
    parentId: 'realm-svartalfheim',
    status: 'healthy',
    zone: 'svartalfheim',
    purpose: 'shop-floor signal source — 01-04 + console, 5/5',
  },
];

export const SEED_NODES: TopologyNode[] = [
  ...REALMS,
  ...CLUSTERS,
  ...HOSTS,
  ...COORDINATORS,
  ...MIMIRS,
  ...CLOUDS,
  ...MODELS,
  ...SERVICES,
  ...RESIDENTS,
  ...SESSIONS,
  ...DEVICES,
];

// ── Edges ─────────────────────────────────────────────────────────────────────

function edge(
  id: string,
  sourceId: string,
  targetId: string,
  kind: TopologyEdge['kind'],
  relationType: TopologyEdge['relationType'],
  confidence: TopologyEdge['confidence'] = 'observed',
): TopologyEdge {
  return { id, sourceId, targetId, kind, relationType, confidence };
}

const MEMORY_EDGES: TopologyEdge[] = [
  ...[
    'ravn-huginn',
    'ravn-kvasir',
    'ravn-forseti',
    'ravn-freyja',
    'ravn-eldhrimnir',
    'ravn-vidar',
  ].map((id) => edge(`mem-${id}`, id, 'mimir-ymir', 'dashed-long', 'writes')),
  edge('mem-njord', 'ravn-njord', 'mimir-noatun', 'dashed-long', 'writes'),
  edge('mem-angrboda', 'ravn-angrboda', 'mimir-noatun', 'dashed-long', 'reads'),
  edge('mem-muninn', 'ravn-muninn', 'mimir-valhalla', 'dashed-long', 'writes', 'declared'),
  edge('mem-ivaldi', 'ravn-ivaldi', 'mimir-ymir', 'dashed-long', 'writes'),
  edge('mem-shared', 'mimir-noatun', 'mimir-ymir', 'soft', 'reads'),
  edge('mem-research', 'run-research', 'mimir-valhalla', 'dashed-long', 'writes', 'declared'),
  edge('mem-coding', 'run-coding', 'mimir-noatun', 'dashed-long', 'writes'),
];

const MODEL_EDGES: TopologyEdge[] = [
  edge('mdl-gw', 'bifrost-ymir', 'svc-inference-gateway', 'solid', 'routes_to'),
  edge('mdl-gw-n', 'bifrost-noatun', 'svc-inference-gateway', 'solid', 'routes_to'),
  edge('mdl-router', 'svc-inference-gateway', 'svc-vllm-router', 'solid', 'routes_to'),
  edge('mdl-nemotron-host', 'model-nemotron', 'host-saehrimnir', 'soft', 'uses'),
  edge('mdl-nemotron-host2', 'model-nemotron', 'host-tanngnjost', 'soft', 'uses'),
  edge('mdl-qwen-host', 'model-qwen36', 'host-tanngrisnir', 'soft', 'uses', 'declared'),
  // The bare-metal residents call the Sparks directly, skipping the gateway.
  edge('mdl-eldhrimnir', 'ravn-eldhrimnir', 'svc-vllm-router', 'solid', 'uses'),
  ...['ravn-huginn', 'ravn-kvasir', 'ravn-forseti', 'ravn-freyja', 'ravn-muninn'].map((id) =>
    edge(`mdl-${id}`, id, 'bifrost-ymir', 'solid', 'uses'),
  ),
  ...['ravn-njord', 'ravn-angrboda'].map((id) =>
    edge(`mdl-${id}`, id, 'bifrost-noatun', 'solid', 'uses'),
  ),
];

const PLATFORM_EDGES: TopologyEdge[] = [
  edge('plat-ting-volundr', 'ting-ymir', 'volundr-ymir', 'solid', 'manages'),
  edge('plat-ting-research', 'ting-ymir', 'run-research', 'dashed-anim', 'manages'),
  edge('plat-ting-coding', 'ting-ymir', 'run-coding', 'dashed-anim', 'manages'),
  edge('plat-guild-ting', 'svc-guild', 'ting-ymir', 'soft', 'uses'),
  edge('plat-keycloak', 'svc-keycloak', 'volundr-ymir', 'soft', 'uses'),
  edge('plat-openbao', 'svc-openbao', 'volundr-ymir', 'soft', 'uses'),
  edge('plat-warden', 'svc-warden-ymir', 'mimir-ymir', 'soft', 'observes'),
  edge('plat-envoy', 'svc-envoy-noatun', 'volundr-noatun', 'solid', 'routes_to'),
  edge('plat-sandbox', 'run-coding', 'svc-openshell', 'dashed-anim', 'uses'),
  edge('plat-openclaw', 'svc-openclaw', 'svc-openshell', 'soft', 'uses'),
  edge('plat-harvester', 'svc-harvester', 'svc-rancher', 'soft', 'manages'),
  edge('plat-baldr', 'host-baldr', 'svc-harvester', 'soft', 'uses'),
  edge('plat-frigg', 'host-frigg', 'svc-harvester', 'soft', 'uses'),
  edge('plat-laevateinn', 'device-laevateinn', 'ravn-ivaldi', 'dashed-anim', 'signals_to'),
  edge('plat-ovfarm', 'device-laevateinn', 'device-ovfarm', 'soft', 'observes'),
  edge('plat-bambuddy', 'device-bambuddy', 'svc-spoolman', 'soft', 'uses'),
  edge('plat-orca', 'device-bambuddy', 'svc-orca', 'soft', 'uses'),
  edge('plat-plex', 'svc-plex', 'svc-arr', 'soft', 'uses'),
];

/** Telemetry from every cluster's mesh node lands on glitnir. */
const OBSERVABILITY_EDGES: TopologyEdge[] = [
  'svc-nats-valaskjalf',
  'svc-nats-ymir',
  'svc-nats-noatun',
  'svc-nats-eitri',
  'svc-nats-jarnvidr',
].map((id) => edge(`obs-${id}`, id, 'svc-tempo', 'soft', 'observes'));

/** NATS peers the clusters with each other — a mesh, not a hub. */
const MESH_EDGES: TopologyEdge[] = [
  ['svc-nats-valaskjalf', 'svc-nats-ymir'],
  ['svc-nats-ymir', 'svc-nats-glitnir'],
  ['svc-nats-glitnir', 'svc-nats-eitri'],
  ['svc-nats-eitri', 'svc-nats-noatun'],
  ['svc-nats-noatun', 'svc-nats-valaskjalf'],
  ['svc-nats-jarnvidr', 'svc-nats-ymir'],
].map(([a, b], i) => edge(`mesh-${i}`, a as string, b as string, 'solid', 'member_of'));

/** Residents that share a mesh peer directly. */
const AGENT_MESH_EDGES: TopologyEdge[] = [
  ['ravn-huginn', 'ravn-eldhrimnir'],
  ['ravn-muninn', 'ravn-kvasir'],
  ['ravn-kvasir', 'ravn-forseti'],
  ['ravn-forseti', 'ravn-freyja'],
  ['ravn-freyja', 'ravn-vidar'],
  ['ravn-ivaldi', 'ravn-angrboda'],
  ['ravn-angrboda', 'ravn-njord'],
].map(([a, b], i) => edge(`amesh-${i}`, a as string, b as string, 'run', 'member_of'));

export const SEED_EDGES: TopologyEdge[] = [
  ...MEMORY_EDGES,
  ...MODEL_EDGES,
  ...PLATFORM_EDGES,
  ...OBSERVABILITY_EDGES,
  ...MESH_EDGES,
  ...AGENT_MESH_EDGES,
];

export const SEED_TOPOLOGY: Topology = {
  nodes: SEED_NODES,
  edges: SEED_EDGES,
  timestamp: TIMESTAMP,
  layoutHints: { mode: 'hybrid', scope: 'world' },
};
