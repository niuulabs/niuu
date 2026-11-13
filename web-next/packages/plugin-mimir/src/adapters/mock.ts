import type { Mount } from '@niuulabs/domain';
import type { IMimirService } from '../ports';
import type { RecentWrite } from '../ports/IMountAdapter';
import type { PageMeta, Page, SearchResult } from '../domain/page';
import type { Source } from '../domain/source';
import type {
  MimirStats,
  MimirGraph,
  KnowledgeGraph,
  GraphNode,
  GraphEdge,
  LiveActivity,
} from '../domain/api-types';
import type { EmbeddingSearchResult } from '../ports/IEmbeddingStore';
import type { EntityMeta } from '../domain/entity';
import type { RegistryMount } from '../domain/registry';
import type { EvalReport, QueryStats } from '../domain/analytics';
import { toPageMeta } from '../domain/page';
import { generateMockMemoryGraph, generateMockLiveActivity } from './mockGraph';
import { buildMockKnowledgeGraph } from './mockKnowledgeGraph';

// ---------------------------------------------------------------------------
// Seed data — Mounts
// ---------------------------------------------------------------------------

const MOCK_MOUNTS: Mount[] = [
  {
    name: 'local',
    role: 'local',
    host: 'localhost',
    url: 'http://localhost:7700',
    priority: 1,
    categories: null,
    status: 'healthy',
    pages: 412,
    sources: 185,
    lastWrite: '2026-04-18T14:22:00Z',
    embedding: 'all-minilm-l6-v2',
    sizeKb: 512,
    desc: "Operator's local knowledge store",
  },
  {
    name: 'shared',
    role: 'shared',
    host: 'kb.niuu.world',
    url: 'https://kb.niuu.world',
    priority: 5,
    categories: null,
    status: 'healthy',
    pages: 1180,
    sources: 842,
    lastWrite: '2026-04-19T09:00:00Z',
    embedding: 'all-mpnet-base-v2',
    sizeKb: 4096,
    desc: 'Realm-wide shared knowledge base',
  },
  {
    name: 'platform',
    role: 'domain',
    host: 'platform-kb.niuu.world',
    url: 'https://platform-kb.niuu.world',
    priority: 3,
    categories: ['infra', 'api', 'arch'],
    status: 'degraded',
    pages: 342,
    sources: 218,
    lastWrite: '2026-04-17T16:45:00Z',
    embedding: 'all-minilm-l6-v2',
    sizeKb: 1024,
    desc: 'Platform-scoped domain knowledge (infra / api / arch)',
  },
  {
    name: 'forge',
    role: 'domain',
    host: 'forge-kb.niuu.world',
    url: 'https://forge-kb.niuu.world',
    priority: 4,
    categories: ['devpod', 'session', 'template'],
    status: 'healthy',
    pages: 184,
    sources: 128,
    lastWrite: '2026-04-19T11:30:00Z',
    embedding: 'all-minilm-l6-v2',
    sizeKb: 384,
    desc: 'Physical dev-session definitions, templates, and forge docs',
  },
];

const MOCK_REGISTRY_MOUNTS: RegistryMount[] = MOCK_MOUNTS.map((mount) => ({
  id: `registry-${mount.name}`,
  name: mount.name,
  kind: mount.url ? 'remote' : 'local',
  lifecycle: 'registered',
  role: mount.role,
  url: mount.url,
  categories: mount.categories,
  authRef: null,
  defaultReadPriority: mount.priority,
  enabled: mount.status !== 'down',
  healthStatus: mount.status === 'healthy' ? 'healthy' : mount.status,
  healthMessage: '',
  desc: mount.desc,
}));

// ---------------------------------------------------------------------------
// Seed data — Pages
// ---------------------------------------------------------------------------

const MOCK_PAGES: Page[] = [
  {
    path: '/arch/overview',
    title: 'Architecture Overview',
    summary: 'High-level view of the Niuu platform architecture.',
    category: 'arch',
    type: 'topic',
    confidence: 'high',
    mounts: ['local', 'shared'],
    updatedAt: '2026-04-18T10:00:00Z',
    updatedBy: 'ravn-fjolnir',
    sourceIds: ['src-001', 'src-002'],
    related: ['/arch/hexagonal', '/api/overview'],
    size: 3200,
    zones: [
      {
        kind: 'key-facts',
        items: [
          'Hexagonal architecture with ports and adapters',
          'Six cognitive regions (Sköll, Hati, Sága, Móði, Váli, Víðarr)',
          'Ting, Volundr, and Niuu are separate modules',
        ],
      },
      {
        kind: 'relationships',
        items: [
          { slug: 'api/overview', note: 'the API rules it implies' },
          { slug: 'infra/k8s', note: 'where it runs' },
        ],
      },
      {
        kind: 'assessment',
        text: 'Architecture is sound and well-documented. Consider extracting shared domain types.',
      },
      {
        kind: 'timeline',
        items: [
          {
            date: '2026-01-08',
            note: 'Compiled from the platform architecture wiki',
            source: 'src-001',
          },
          { date: '2026-02-19', note: 'Confirmed by the hexagonal ADR', source: 'src-002' },
          {
            date: '2026-04-18',
            note: 'belief revised: three modules → Ting, Volundr, and Niuu are separate modules',
            source: 'ravn-fjolnir',
          },
        ],
      },
    ],
  },
  {
    path: '/api/overview',
    title: 'API Design Guidelines',
    summary: 'Standards and conventions for Niuu REST APIs.',
    category: 'api',
    type: 'directive',
    confidence: 'high',
    mounts: ['shared'],
    updatedAt: '2026-04-15T09:30:00Z',
    updatedBy: 'ravn-skald',
    sourceIds: ['src-003'],
    related: ['/arch/overview'],
    size: 2100,
    zones: [
      {
        kind: 'key-facts',
        items: [
          'Raw SQL with asyncpg — no ORM',
          'Parameterised queries only',
          'Hexagonal adapter pattern for all infrastructure',
        ],
      },
      {
        kind: 'timeline',
        items: [
          { date: '2026-01-10', note: 'Initial guidelines published', source: 'src-003' },
          { date: '2026-03-20', note: 'Added asyncpg section', source: 'src-003' },
        ],
      },
    ],
  },
  {
    path: '/infra/k8s',
    title: 'Kubernetes Deployment',
    summary: 'Kubernetes-native deployment patterns for the Niuu platform.',
    category: 'infra',
    type: 'topic',
    confidence: 'medium',
    flagged: true,
    mounts: ['platform'],
    updatedAt: '2026-04-10T14:00:00Z',
    updatedBy: 'ravn-fjolnir',
    sourceIds: ['src-004', 'src-005'],
    related: ['/infra/envoy', '/arch/overview'],
    size: 4500,
    zones: [
      {
        kind: 'key-facts',
        items: [
          'Uses `migrate` for schema migrations (not Alembic)',
          'Envoy as API gateway with OIDC',
          'Services exposed as ClusterIP internally',
        ],
      },
    ],
  },
  {
    path: '/concepts/drive-loop',
    title: 'Drive loop',
    summary: 'Per-ravn scheduling loop — observe, decide, act, write.',
    type: 'entity' as const,
    category: 'concept',
    entityType: 'concept',
    confidence: 'medium' as const,
    mounts: ['shared'],
    updatedBy: 'ravn-saga',
    flagged: false,
    sourceIds: ['src-004'],
    related: ['/arch/overview'],
    updatedAt: '2026-04-17T14:00:00Z',
    size: 800,
    zones: [],
  },
  {
    path: '/entities/project-niuu',
    title: 'Project Niuu',
    summary: 'Homegrown agentic-infra platform — hex-arch, Norse naming.',
    type: 'entity' as const,
    category: 'project',
    entityType: 'project',
    confidence: 'high' as const,
    mounts: ['shared', 'local'],
    updatedBy: 'ravn-fjolnir',
    flagged: false,
    sourceIds: ['src-001'],
    related: ['/arch/overview', '/api/overview'],
    updatedAt: '2026-04-18T09:00:00Z',
    size: 1200,
    zones: [],
  },
  {
    path: '/entities/person-karpathy',
    title: 'Andrej Karpathy',
    summary: 'AI researcher, advocate for small focused LMs and minimal architectures.',
    type: 'entity' as const,
    category: 'person',
    entityType: 'person',
    confidence: 'high' as const,
    mounts: ['local', 'shared'],
    updatedBy: 'ravn-skald',
    flagged: false,
    sourceIds: ['src-002'],
    related: [],
    updatedAt: '2026-04-17T14:22:00Z',
    size: 600,
    zones: [],
  },
  {
    path: '/agents/raven-runtime',
    title: 'Raven agent runtime',
    summary: 'Drive loop, tool registry, composite mimir binding, scheduled triggers.',
    type: 'topic' as const,
    category: 'component',
    entityType: undefined,
    confidence: 'high' as const,
    mounts: ['local', 'shared'],
    updatedBy: 'ravn-fjolnir',
    flagged: false,
    sourceIds: ['src-001', 'src-003'],
    related: ['/concepts/drive-loop'],
    updatedAt: '2026-04-18T14:22:00Z',
    size: 1800,
    zones: [],
  },
  {
    path: '/index',
    title: 'Content catalog',
    summary: 'Auto-generated page directory.',
    type: 'topic' as const,
    category: 'infra',
    entityType: undefined,
    confidence: 'high' as const,
    mounts: ['local', 'shared', 'platform', 'forge'],
    updatedBy: 'ravn-skald',
    flagged: false,
    sourceIds: [],
    related: [],
    updatedAt: '2026-04-19T09:00:00Z',
    size: 400,
    zones: [],
  },
  {
    path: '/concepts/hexagonal-architecture',
    title: 'Hexagonal Architecture',
    summary: 'Ports-and-adapters backbone. Sleipnir fans events; Bifröst gates inference.',
    type: 'decision' as const,
    category: 'arch',
    entityType: undefined,
    confidence: 'high' as const,
    mounts: ['local', 'shared'],
    updatedBy: 'ravn-fjolnir',
    flagged: false,
    sourceIds: ['src-001'],
    related: ['/arch/overview'],
    updatedAt: '2026-04-18T11:30:00Z',
    size: 1400,
    zones: [],
  },
  {
    path: '/niuu/architecture',
    title: 'Niuu architecture',
    summary:
      'Ports and adapters backbone. Sleipnir fans events; Bifröst gates inference; ravens run against their bindings.',
    type: 'topic' as const,
    category: 'arch',
    entityType: undefined,
    confidence: 'high' as const,
    mounts: ['local', 'shared'],
    updatedBy: 'ravn-fjolnir',
    flagged: true,
    sourceIds: ['src-001', 'src-002'],
    related: ['/concepts/hexagonal-architecture', '/arch/overview'],
    updatedAt: '2026-04-19T14:22:00Z',
    size: 2400,
    zones: [],
  },
];

// ---------------------------------------------------------------------------
// Seed data — Sources
// ---------------------------------------------------------------------------

const MOCK_SOURCES: Source[] = [
  {
    id: 'src-001',
    title: 'Niuu Platform Architecture — internal wiki',
    originType: 'web',
    originUrl: 'https://wiki.niuu.world/arch/platform',
    ingestedAt: '2026-04-10T08:00:00Z',
    ingestAgent: 'ravn-fjolnir',
    compiledInto: ['/arch/overview'],
    content:
      'The Niuu platform uses hexagonal architecture with ports and adapters. The six cognitive regions are Sköll, Hati, Sága, Móði, Váli, and Víðarr.',
  },
  {
    id: 'src-002',
    title: 'ADR-001: hexagonal architecture decision',
    originType: 'file',
    originPath: '/docs/adr/001-hexagonal.md',
    ingestedAt: '2026-04-11T10:30:00Z',
    ingestAgent: 'ravn-fjolnir',
    compiledInto: ['/arch/overview'],
    content:
      'We adopt hexagonal architecture (ports and adapters) to decouple business logic from infrastructure. Regions import from ports only.',
  },
  {
    id: 'src-003',
    title: 'API guidelines RFC',
    originType: 'mail',
    ingestedAt: '2026-04-12T14:00:00Z',
    ingestAgent: 'ravn-skald',
    compiledInto: ['/api/overview'],
    content:
      'RFC for standardising REST API conventions across Niuu services. Raw SQL with asyncpg.',
  },
  {
    id: 'src-004',
    title: 'Kubernetes deployment runbook',
    originType: 'file',
    originPath: '/ops/runbooks/k8s-deploy.md',
    ingestedAt: '2026-04-08T09:00:00Z',
    ingestAgent: 'ravn-fjolnir',
    compiledInto: ['/infra/k8s'],
    content: 'Step-by-step guide for deploying Niuu services to Kubernetes.',
  },
  {
    id: 'src-005',
    title: 'Kubernetes patterns — arxiv survey',
    originType: 'arxiv',
    originUrl: 'https://arxiv.org/abs/2406.01234',
    ingestedAt: '2026-04-09T11:00:00Z',
    ingestAgent: 'ravn-fjolnir',
    compiledInto: ['/infra/k8s'],
    content: 'Survey of cloud-native deployment patterns and service mesh architectures.',
  },
  {
    id: 'src-006',
    title: 'Niuu blog: idle-time knowledge synthesis',
    originType: 'rss',
    originUrl: 'https://blog.niuu.world/feed.xml',
    ingestedAt: '2026-04-17T06:00:00Z',
    ingestAgent: 'ravn-skald',
    compiledInto: [],
    content: 'Blog post on the design of idle-time knowledge synthesis in Niuu.',
  },
  {
    id: 'src-007',
    title: 'Architecture discussion — session transcript',
    originType: 'chat',
    ingestedAt: '2026-04-18T15:00:00Z',
    ingestAgent: 'ravn-fjolnir',
    compiledInto: [],
    content: 'Team session discussing the module boundary rules for Ting, Volundr, and Niuu.',
  },
];

// ---------------------------------------------------------------------------
// Seed data — entity pages
// ---------------------------------------------------------------------------

const MOCK_ENTITY_PAGES: Page[] = [
  {
    path: '/entities/niuulabs',
    title: 'Niuu Labs',
    summary: 'The organisation behind the Niuu platform.',
    category: 'org',
    type: 'entity',
    entityType: 'org',
    confidence: 'high',
    mounts: ['shared'],
    updatedAt: '2026-04-18T08:00:00Z',
    updatedBy: 'ravn-fjolnir',
    sourceIds: ['src-001'],
    related: ['/entities/ting', '/entities/volundr', '/entities/mimir'],
    size: 800,
    zones: [
      {
        kind: 'relationships',
        items: [
          { slug: '/entities/ting', note: 'builds' },
          { slug: '/entities/volundr', note: 'builds' },
          { slug: '/entities/mimir', note: 'builds' },
        ],
      },
    ],
  },
  {
    path: '/entities/hexagonal-arch',
    title: 'Hexagonal Architecture',
    summary:
      'Software architecture pattern separating business logic from infrastructure via ports and adapters.',
    category: 'concept',
    type: 'entity',
    entityType: 'concept',
    confidence: 'high',
    mounts: ['shared', 'local'],
    updatedAt: '2026-04-17T12:00:00Z',
    updatedBy: 'ravn-skald',
    sourceIds: ['src-002'],
    related: ['/arch/overview', '/entities/asyncpg'],
    size: 600,
    zones: [
      {
        kind: 'key-facts',
        items: [
          'Business logic depends on ports (interfaces), never on adapters',
          'Adapters implement ports and can be swapped without changing business logic',
        ],
      },
    ],
  },
  {
    path: '/entities/ting',
    title: 'Ting',
    summary: 'The autonomous dispatcher module of the Niuu platform.',
    category: 'component',
    type: 'entity',
    entityType: 'component',
    confidence: 'high',
    mounts: ['local', 'shared'],
    updatedAt: '2026-04-16T10:00:00Z',
    updatedBy: 'ravn-fjolnir',
    sourceIds: ['src-006'],
    related: ['/entities/niuulabs', '/arch/overview'],
    size: 900,
    zones: [
      {
        kind: 'relationships',
        items: [{ slug: '/entities/niuulabs', note: 'maintained by' }],
      },
    ],
  },
  {
    path: '/entities/asyncpg',
    title: 'asyncpg',
    summary: 'High-performance async PostgreSQL driver for Python.',
    category: 'technology',
    type: 'entity',
    entityType: 'technology',
    confidence: 'medium',
    mounts: ['shared'],
    updatedAt: '2026-04-15T09:00:00Z',
    updatedBy: 'ravn-skald',
    sourceIds: ['src-003'],
    related: ['/api/overview', '/entities/hexagonal-arch'],
    size: 450,
    zones: [],
  },
];

const ALL_PAGES = [...MOCK_PAGES, ...MOCK_ENTITY_PAGES];

// ---------------------------------------------------------------------------
// Seed data — graph
// ---------------------------------------------------------------------------

/**
 * The memory-kind leaf vocabulary `domain/memoryKinds.ts`'s `kindGroup()`
 * recognises (its entity sub-type set plus its direct-group set). Not
 * exported by that module — it only exports the grouping function and the
 * legend metadata — so the mock generator keeps its own copy of the leaf
 * values it needs to produce a representative spread.
 */
type MockKind =
  | 'topic'
  | 'person'
  | 'project'
  | 'concept'
  | 'technology'
  | 'organization'
  | 'strategy'
  | 'decision'
  | 'directive'
  | 'preference'
  | 'goal'
  | 'observation';

/**
 * `EntityMeta['entityKind']` predates the memory-kind vocabulary and uses
 * shorter/different leaf names for two of the six sub-types. Normalise them
 * so hand-written entity pages colour correctly in the memory scene
 * alongside the generated ones.
 */
function entityTypeToMemoryKind(entityType: string | undefined): MockKind {
  switch (entityType) {
    case 'org':
      return 'organization';
    case 'component':
      return 'technology';
    case 'person':
    case 'project':
    case 'concept':
    case 'technology':
    case 'organization':
    case 'strategy':
      return entityType;
    default:
      return 'concept';
  }
}

function pageToMemoryKind(page: Page): MockKind {
  if (page.type === 'entity') return entityTypeToMemoryKind(page.entityType);
  switch (page.type) {
    case 'decision':
    case 'directive':
    case 'preference':
      return page.type;
    case 'topic':
    default:
      return 'topic';
  }
}

/** Fixed reference instant the curated mock timestamps are already anchored to. */
const MOCK_GRAPH_NOW = Date.parse('2026-04-19T12:00:00Z');

/** Target synthetic page count — keeps the total graph in the ~600-900 page range. */
const MOCK_SYNTHETIC_PAGE_COUNT = 750;

const REAL_GRAPH_NODES: GraphNode[] = ALL_PAGES.map((p) => ({
  id: p.path,
  title: p.title,
  category: p.category,
  path: p.path,
  mount: p.mounts[0] ?? 'local',
  kind: pageToMemoryKind(p),
  updatedAt: p.updatedAt,
  // The hand-written pages carry no separate "first observed" date; treating
  // their last update as their birth is a reasonable simplification for a
  // handful of hero pages that were authored once and refined since.
  firstSeen: p.updatedAt,
  confidence: p.confidence,
}));

const CURATED_GRAPH_EDGES: GraphEdge[] = [
  { source: '/arch/overview', target: '/api/overview', relation: 'explains' },
  { source: '/infra/k8s', target: '/arch/overview', relation: 'depends_on' },
  { source: '/arch/overview', target: '/entities/hexagonal-arch', relation: 'part_of' },
  { source: '/entities/niuulabs', target: '/entities/ting', relation: 'part_of' },
  { source: '/entities/ting', target: '/arch/overview', relation: 'depends_on' },
  { source: '/api/overview', target: '/entities/asyncpg', relation: 'depends_on' },
  { source: '/entities/hexagonal-arch', target: '/entities/asyncpg', relation: 'depends_on' },
];

const SYNTHETIC_GRAPH = generateMockMemoryGraph({
  seed: 20260419,
  count: MOCK_SYNTHETIC_PAGE_COUNT,
  mounts: MOCK_MOUNTS.map((m) => m.name),
  seedNodes: REAL_GRAPH_NODES,
  now: MOCK_GRAPH_NOW,
});

/** The mock graph keyed by page path, before node ids are encoded. */
const MOCK_PATH_GRAPH: MimirGraph = {
  nodes: [...REAL_GRAPH_NODES, ...SYNTHETIC_GRAPH.nodes],
  edges: [...CURATED_GRAPH_EDGES, ...SYNTHETIC_GRAPH.edges],
};

/**
 * The same graph as `GET /graph` shapes it: node ids are opaque
 * `<mount>:<path>` strings, so screens that confuse an id with a path break
 * in the dev app exactly as they would against the real service.
 */
const MOCK_GRAPH: MimirGraph = (() => {
  const idFor = new Map(
    MOCK_PATH_GRAPH.nodes.map((node) => [
      node.id,
      `${encodeURIComponent(node.mount)}:${encodeURIComponent(node.path)}`,
    ]),
  );
  return {
    nodes: MOCK_PATH_GRAPH.nodes.map((node) => ({ ...node, id: idFor.get(node.id)! })),
    edges: MOCK_PATH_GRAPH.edges.map((edge) => ({
      ...edge,
      source: idFor.get(edge.source)!,
      target: idFor.get(edge.target)!,
    })),
  };
})();

// ---------------------------------------------------------------------------
// Seed data — Recent writes activity feed
// ---------------------------------------------------------------------------

const MOCK_RECENT_WRITES: RecentWrite[] = [
  {
    id: 'ev-001',
    timestamp: '2026-04-19T09:02:00Z',
    mount: 'shared',
    page: '/arch/overview',
    ravn: 'ravn-fjolnir',
    kind: 'write',
    message: 'updated key-facts zone after hexagonal ADR review',
  },
  {
    id: 'ev-002',
    timestamp: '2026-04-19T08:30:00Z',
    mount: 'local',
    page: '/api/overview',
    ravn: 'ravn-skald',
    kind: 'compile',
    message: 'recompiled from 2 sources after RFC merge',
  },
  {
    id: 'ev-005',
    timestamp: '2026-04-18T14:22:00Z',
    mount: 'local',
    page: '/arch/overview',
    ravn: 'ravn-fjolnir',
    kind: 'write',
    message: 'added timeline entry for module boundary decision',
  },
  {
    id: 'ev-006',
    timestamp: '2026-04-18T12:00:00Z',
    mount: 'platform',
    page: '/infra/k8s',
    ravn: 'ravn-fjolnir',
    kind: 'write',
    message: 'updated deployment patterns from arxiv survey src-005',
  },
  {
    id: 'ev-007',
    timestamp: '2026-04-18T10:00:00Z',
    mount: 'shared',
    page: '',
    ravn: 'ravn-skald',
    kind: 'compile',
    message: 'batch compile: 3 pages updated from new RSS ingest',
  },
  {
    id: 'ev-008',
    timestamp: '2026-04-17T16:45:00Z',
    mount: 'platform',
    page: '/api/overview',
    ravn: 'ravn-fjolnir',
    kind: 'write',
    message: 'added asyncpg guidelines from ADR-003',
  },
];

// ---------------------------------------------------------------------------
// Seed data — retrieval analytics
// ---------------------------------------------------------------------------

const MOCK_EVAL_REPORT: EvalReport = {
  generatedAt: '2026-04-19T04:30:00Z',
  overall: { precisionAt5: 0.82, mrr: 0.74, recallAt10: 0.91 },
  byCategory: {
    arch: { precisionAt5: 0.88, mrr: 0.81, recallAt10: 0.95 },
    api: { precisionAt5: 0.85, mrr: 0.78, recallAt10: 0.93 },
    infra: { precisionAt5: 0.79, mrr: 0.7, recallAt10: 0.88 },
    concept: { precisionAt5: 0.74, mrr: 0.66, recallAt10: 0.86 },
  },
  queryCount: 120,
};

const MOCK_QUERY_STATS: QueryStats = {
  total: 1843,
  zeroResultCount: 37,
  recent: [
    { ts: '2026-04-19T10:14:02Z', query: 'hexagonal architecture', resultCount: 6 },
    { ts: '2026-04-19T10:02:48Z', query: 'sleipnir retention policy', resultCount: 0 },
    { ts: '2026-04-19T09:54:31Z', query: 'asyncpg pool sizing', resultCount: 3 },
    { ts: '2026-04-19T09:41:12Z', query: 'resin print profiles', resultCount: 2 },
    { ts: '2026-04-19T09:22:10Z', query: 'bifrost gpu quota', resultCount: 0 },
    { ts: '2026-04-19T08:58:44Z', query: 'cluster upgrade', resultCount: 2 },
    { ts: '2026-04-19T08:31:09Z', query: 'kubernetes deployment', resultCount: 5 },
    { ts: '2026-04-19T08:12:55Z', query: 'idle synthesis cadence', resultCount: 4 },
  ],
};

// ---------------------------------------------------------------------------
// Debug score breakdowns
// ---------------------------------------------------------------------------

const CONFIDENCE_FACTOR: Record<Page['confidence'], number> = {
  high: 1.15,
  medium: 1.0,
  low: 0.85,
};

function buildScoreBreakdown(page: Page, score: number, index: number): Record<string, number> {
  const round = (n: number) => Number(n.toFixed(2));
  return {
    base: round(score * 0.7),
    recency: round(Math.max(0.5, 0.93 - index * 0.04)),
    title_match: round(Math.max(0.8, 1.25 - index * 0.06)),
    confidence: CONFIDENCE_FACTOR[page.confidence],
    backlinks: round(1 + Math.min(0.2, (page.related?.length ?? 0) * 0.04)),
    zone: round(page.zones && page.zones.length > 0 ? 1.1 : 1.0),
    final: round(score),
  };
}

// ---------------------------------------------------------------------------
// Mock adapter
// ---------------------------------------------------------------------------

export function createMimirMockAdapter(): IMimirService {
  // Mutable copies for write operations
  let sources = [...MOCK_SOURCES];
  let registryMounts = [...MOCK_REGISTRY_MOUNTS];
  return {
    mounts: {
      async listMounts(): Promise<Mount[]> {
        return MOCK_MOUNTS;
      },

      async listRegistryMounts(): Promise<RegistryMount[]> {
        return [...registryMounts];
      },

      async createRegistryMount(mount: Omit<RegistryMount, 'id'>): Promise<RegistryMount> {
        const created = { ...mount, id: `registry-${Date.now()}` };
        registryMounts = [...registryMounts, created];
        return created;
      },

      async updateRegistryMount(
        id: string,
        mount: Omit<RegistryMount, 'id'>,
      ): Promise<RegistryMount> {
        const updated = { ...mount, id };
        registryMounts = registryMounts.map((entry) => (entry.id === id ? updated : entry));
        return updated;
      },

      async deleteRegistryMount(id: string): Promise<void> {
        registryMounts = registryMounts.filter((entry) => entry.id !== id);
      },

      async getRecentWrites(limit = 20): Promise<RecentWrite[]> {
        return MOCK_RECENT_WRITES.slice(0, limit);
      },

      async getEvalReport(): Promise<EvalReport | null> {
        return MOCK_EVAL_REPORT;
      },

      async getQueryStats(): Promise<QueryStats | null> {
        return MOCK_QUERY_STATS;
      },
    },

    pages: {
      async getStats(): Promise<MimirStats> {
        const categories = [...new Set(MOCK_PAGES.map((p) => p.category))];
        return {
          pageCount: MOCK_PAGES.length,
          categories,
          healthy: MOCK_MOUNTS.every((m) => m.status !== 'down'),
        };
      },

      async listPages(options): Promise<PageMeta[]> {
        let pages = MOCK_PAGES;
        if (options?.mountName) {
          pages = pages.filter((p) => p.mounts.includes(options.mountName!));
        }
        if (options?.category) {
          pages = pages.filter((p) => p.category === options.category);
        }
        return pages.map(toPageMeta);
      },

      async getPage(path: string, mountName?: string): Promise<Page | null> {
        const page = ALL_PAGES.find((candidate) => candidate.path === path) ?? null;
        if (!page) return null;
        if (mountName && !page.mounts.includes(mountName)) return null;
        return page;
      },

      async upsertPage(): Promise<void> {
        // no-op in mock
      },

      async search(
        query: string,
        _mode,
        mountName?: string,
        debug?: boolean,
      ): Promise<SearchResult[]> {
        const q = query.toLowerCase();
        return MOCK_PAGES.filter(
          (p) =>
            (p.title.toLowerCase().includes(q) || p.summary.toLowerCase().includes(q)) &&
            (!mountName || p.mounts.includes(mountName)),
        ).map((p, i) => {
          const score = Math.max(0.5, 0.95 - i * 0.1);
          return {
            path: p.path,
            title: p.title,
            summary: p.summary,
            category: p.category,
            type: p.type,
            confidence: p.confidence,
            score,
            mounts: p.mounts,
            scoreBreakdown: debug ? buildScoreBreakdown(p, score, i) : undefined,
          };
        });
      },

      async listSources(options): Promise<Source[]> {
        let filtered = sources;
        if (options?.originType) {
          filtered = filtered.filter((s) => s.originType === options.originType);
        }
        if (options?.mountName) {
          // Filter sources that are attributed to pages on this mount
          const mountPages = MOCK_PAGES.filter((p) => p.mounts.includes(options.mountName!)).map(
            (p) => p.path,
          );
          filtered = filtered.filter((s) =>
            s.compiledInto.some((path) => mountPages.includes(path)),
          );
        }
        return filtered;
      },

      async getPageSources(path: string): Promise<Source[]> {
        const page = MOCK_PAGES.find((p) => p.path === path);
        if (!page) return [];
        return sources.filter((s) => page.sourceIds.includes(s.id));
      },

      async ingestUrl(url: string): Promise<Source> {
        const id = `src-${Date.now()}`;
        const source: Source = {
          id,
          title: url,
          originType: 'web',
          originUrl: url,
          ingestedAt: new Date().toISOString(),
          ingestAgent: 'ravn-fjolnir',
          compiledInto: [],
          content: '',
        };
        sources = [source, ...sources];
        return source;
      },

      async ingestFile(file: File): Promise<Source> {
        const id = `src-${Date.now()}`;
        const source: Source = {
          id,
          title: file.name,
          originType: 'file',
          originPath: file.name,
          ingestedAt: new Date().toISOString(),
          ingestAgent: 'ravn-fjolnir',
          compiledInto: [],
          content: '',
        };
        sources = [source, ...sources];
        return source;
      },

      async getKnowledgeGraph(options): Promise<KnowledgeGraph> {
        const graph = buildMockKnowledgeGraph(MOCK_GRAPH, Date.now());
        const layers = options?.layers ?? [];
        if (layers.length === 0) return graph;
        const wanted = new Set(layers);
        const nodes = graph.nodes.filter((n) => wanted.has(n.layer!));
        const ids = new Set(nodes.map((n) => n.id));
        return {
          layers: graph.layers.filter((l) => wanted.has(l.id)),
          nodes,
          edges: graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target)),
          unresolved: graph.unresolved.filter((u) => ids.has(u.source)),
        };
      },

      async getGraph(options): Promise<MimirGraph> {
        if (!options?.mountName) {
          return MOCK_GRAPH;
        }
        const mountNodeIds = new Set(
          MOCK_GRAPH.nodes.filter((n) => n.mount === options.mountName).map((n) => n.id),
        );
        return {
          nodes: MOCK_GRAPH.nodes.filter((n) => mountNodeIds.has(n.id)),
          edges: MOCK_GRAPH.edges.filter(
            (e) => mountNodeIds.has(e.source) && mountNodeIds.has(e.target),
          ),
        };
      },

      async getLiveActivity(options): Promise<LiveActivity[]> {
        const targets = MOCK_PAGES.slice(0, 6).map((p) => ({
          mount: p.mounts[0] ?? 'local',
          path: p.path,
        }));
        return generateMockLiveActivity(targets, Date.now(), options?.since);
      },

      async listEntities(options): Promise<EntityMeta[]> {
        let entities = MOCK_ENTITY_PAGES;
        if (options?.kind) {
          entities = entities.filter((p) => p.entityType === options.kind);
        }
        return entities.map((p) => ({
          path: p.path,
          title: p.title,
          entityKind: (p.entityType ?? 'concept') as EntityMeta['entityKind'],
          summary: p.summary,
          relationshipCount:
            p.zones
              ?.filter((z) => z.kind === 'relationships')
              .flatMap((z) => (z.kind === 'relationships' ? z.items : [])).length ?? 0,
        }));
      },
    },

    embeddings: {
      async semanticSearch(_query: string, topK = 10): Promise<EmbeddingSearchResult[]> {
        return MOCK_PAGES.slice(0, topK).map((p, i) => ({
          path: p.path,
          title: p.title,
          summary: p.summary,
          score: Math.max(0.5, 0.95 - i * 0.15),
          mountName: p.mounts[0] ?? 'local',
        }));
      },
    },
  };
}
