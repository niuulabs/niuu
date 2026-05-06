import type { Mount } from '@niuulabs/domain';
import type { IMimirService } from '../ports';
import type { RecentWrite } from '../ports/IMountAdapter';
import type { PageMeta, Page, SearchResult } from '../domain/page';
import type { LintIssue, LintReport, DreamCycle, ActivityEvent } from '../domain/lint';
import type { Source } from '../domain/source';
import type { MimirStats, MimirGraph } from '../domain/api-types';
import type { EmbeddingSearchResult } from '../ports/IEmbeddingStore';
import type { EntityMeta } from '../domain/entity';
import type { WriteRoutingRule } from '../domain/routing';
import type { RavnBinding } from '../domain/ravn-binding';
import type { RegistryMount } from '../domain/registry';
import { tallySeverity } from '../domain/lint';
import { toPageMeta } from '../domain/page';

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
    lintIssues: 8,
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
    lintIssues: 18,
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
    lintIssues: 14,
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
    lintIssues: 6,
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
  path: '',
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
          'Tyr, Volundr, and Niuu are separate modules',
        ],
      },
      {
        kind: 'assessment',
        text: 'Architecture is sound and well-documented. Consider extracting shared domain types.',
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
    summary: 'Drive loop, tool registry, composite mimir binding, dream-cycle trigger.',
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
    title: 'Niuu blog: ravn dream cycles',
    originType: 'rss',
    originUrl: 'https://blog.niuu.world/feed.xml',
    ingestedAt: '2026-04-17T06:00:00Z',
    ingestAgent: 'ravn-skald',
    compiledInto: [],
    content: 'Blog post on the design of Niuu dream cycles for idle-time knowledge synthesis.',
  },
  {
    id: 'src-007',
    title: 'Architecture discussion — session transcript',
    originType: 'chat',
    ingestedAt: '2026-04-18T15:00:00Z',
    ingestAgent: 'ravn-fjolnir',
    compiledInto: [],
    content: 'Team session discussing the module boundary rules for Tyr, Volundr, and Niuu.',
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
    related: ['/entities/tyr', '/entities/volundr', '/entities/mimir'],
    size: 800,
    zones: [
      {
        kind: 'relationships',
        items: [
          { slug: '/entities/tyr', note: 'builds' },
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
    path: '/entities/tyr',
    title: 'Tyr',
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

const MOCK_GRAPH: MimirGraph = {
  nodes: ALL_PAGES.map((p) => ({
    id: p.path,
    title: p.title,
    category: p.category,
  })),
  edges: [
    { source: '/arch/overview', target: '/api/overview' },
    { source: '/infra/k8s', target: '/arch/overview' },
    { source: '/arch/overview', target: '/entities/hexagonal-arch' },
    { source: '/entities/niuulabs', target: '/entities/tyr' },
    { source: '/entities/tyr', target: '/arch/overview' },
    { source: '/api/overview', target: '/entities/asyncpg' },
    { source: '/entities/hexagonal-arch', target: '/entities/asyncpg' },
  ],
};

// ---------------------------------------------------------------------------
// Seed data — Lint issues
// ---------------------------------------------------------------------------

const INITIAL_LINT_ISSUES: LintIssue[] = [
  {
    id: 'lint-001',
    rule: 'L05',
    severity: 'error',
    page: '/arch/hexagonal',
    mount: 'local',
    autoFix: false,
    message: 'Broken wikilink: [[ports/overview]] — target page does not exist',
  },
  {
    id: 'lint-002',
    rule: 'L07',
    severity: 'warn',
    page: '/infra/legacy-proxy',
    mount: 'platform',
    assignee: 'ravn-skald',
    autoFix: true,
    message: 'Orphan page — no inbound links from any other page',
  },
  {
    id: 'lint-003',
    rule: 'L12',
    severity: 'info',
    page: '/api/overview',
    mount: 'shared',
    autoFix: true,
    message: 'Missing required frontmatter field: `owner`',
  },
  {
    id: 'lint-004',
    rule: 'L02',
    severity: 'warn',
    page: '/infra/k8s',
    mount: 'platform',
    autoFix: true,
    message: 'Stale page — source updated 3 days ago but page not recompiled',
  },
  {
    id: 'lint-005',
    rule: 'L11',
    severity: 'error',
    page: '/arch/overview',
    mount: 'shared',
    assignee: 'ravn-fjolnir',
    autoFix: true,
    message: 'Stale mount index — index out of sync with page store',
  },
];

// ---------------------------------------------------------------------------
// Seed data — Dream cycles
// ---------------------------------------------------------------------------

const MOCK_DREAM_CYCLES: DreamCycle[] = [
  {
    id: 'dream-001',
    timestamp: '2026-04-19T03:00:00Z',
    ravn: 'ravn-fjolnir',
    mounts: ['local', 'shared'],
    pagesUpdated: 8,
    entitiesCreated: 2,
    lintFixes: 1,
    durationMs: 42000,
  },
  {
    id: 'dream-002',
    timestamp: '2026-04-18T03:00:00Z',
    ravn: 'ravn-fjolnir',
    mounts: ['shared', 'platform'],
    pagesUpdated: 14,
    entitiesCreated: 5,
    lintFixes: 3,
    durationMs: 67000,
  },
  {
    id: 'dream-003',
    timestamp: '2026-04-17T03:00:00Z',
    ravn: 'ravn-skald',
    mounts: ['platform'],
    pagesUpdated: 6,
    entitiesCreated: 1,
    lintFixes: 0,
    durationMs: 28000,
  },
  {
    id: 'dream-004',
    timestamp: '2026-04-16T03:00:00Z',
    ravn: 'ravn-fjolnir',
    mounts: ['local', 'shared', 'platform'],
    pagesUpdated: 22,
    entitiesCreated: 8,
    lintFixes: 5,
    durationMs: 91000,
  },
];

// ---------------------------------------------------------------------------
// Seed data — write-routing rules
// ---------------------------------------------------------------------------

const INITIAL_ROUTING_RULES: WriteRoutingRule[] = [
  {
    id: 'route-001',
    prefix: '/infra',
    mountName: 'platform',
    priority: 5,
    active: true,
    desc: 'Infrastructure pages → platform mount',
  },
  {
    id: 'route-002',
    prefix: '/api',
    mountName: 'shared',
    priority: 10,
    active: true,
    desc: 'API docs → shared mount',
  },
  {
    id: 'route-003',
    prefix: '/entities',
    mountName: 'shared',
    priority: 15,
    active: true,
    desc: 'Entity pages → shared mount',
  },
  {
    id: 'route-004',
    prefix: '/',
    mountName: 'local',
    priority: 99,
    active: true,
    desc: 'Catch-all → local mount',
  },
];

// ---------------------------------------------------------------------------
// Seed data — ravn bindings
// ---------------------------------------------------------------------------

const MOCK_RAVN_BINDINGS: RavnBinding[] = [
  {
    ravnId: 'ravn-fjolnir',
    ravnRune: 'ᚠ',
    role: 'index',
    state: 'active',
    mountNames: ['local', 'shared', 'platform'],
    writeMount: 'local',
    lastDream: MOCK_DREAM_CYCLES[0] ?? null,
    bio: 'Synthesises infrastructure documentation from git commits and runbooks',
    pagesTouched: 52,
    expertise: ['infra', 'api', 'arch'],
    tools: ['mimir', 'web', 'file', 'ravn'],
  },
  {
    ravnId: 'ravn-skald',
    ravnRune: 'ᛋ',
    role: 'build',
    state: 'idle',
    mountNames: ['shared', 'platform'],
    writeMount: 'shared',
    lastDream: MOCK_DREAM_CYCLES[2] ?? null,
    bio: 'Compiles API guidelines and architectural decisions from RFC discussions',
    pagesTouched: 28,
    expertise: ['api', 'observability'],
    tools: ['mimir', 'web'],
  },
  {
    ravnId: 'ravn-galdra',
    ravnRune: 'ᚷ',
    role: 'verify',
    state: 'offline',
    mountNames: ['shared'],
    writeMount: 'shared',
    lastDream: null,
    bio: 'Verifies knowledge consistency and resolves broken wikilinks across mounts',
    pagesTouched: 0,
    expertise: ['lint', 'wikilinks'],
    tools: ['mimir', 'lint-fix'],
  },
  {
    ravnId: 'ravn-saga',
    ravnRune: 'ᛊ',
    role: 'index',
    state: 'idle',
    mountNames: ['shared', 'forge'],
    writeMount: 'shared',
    lastDream: MOCK_DREAM_CYCLES[1] ?? null,
    bio: 'Indexes saga histories and raid outcomes into long-term knowledge',
    pagesTouched: 34,
    expertise: ['sagas', 'raids'],
    tools: ['mimir', 'tyr'],
  },
  {
    ravnId: 'ravn-eir',
    ravnRune: 'ᛖ',
    role: 'verify',
    state: 'active',
    mountNames: ['local', 'shared'],
    writeMount: 'local',
    lastDream: MOCK_DREAM_CYCLES[0] ?? null,
    bio: 'Monitors deployment health and synthesises runbook updates',
    pagesTouched: 19,
    expertise: ['monitoring', 'runbooks'],
    tools: ['mimir', 'web', 'lint-fix'],
  },
  {
    ravnId: 'ravn-vor',
    ravnRune: 'ᚹ',
    role: 'build',
    state: 'idle',
    mountNames: ['platform'],
    writeMount: 'platform',
    lastDream: null,
    bio: 'Compiles and cross-references ADR documents with implementation state',
    pagesTouched: 11,
    expertise: ['adr', 'compliance'],
    tools: ['mimir', 'file'],
  },
];

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
    id: 'ev-003',
    timestamp: '2026-04-19T03:01:00Z',
    mount: 'shared',
    page: '',
    ravn: 'ravn-fjolnir',
    kind: 'dream',
    message: 'dream cycle complete — 8 pages updated, 2 entities created',
  },
  {
    id: 'ev-004',
    timestamp: '2026-04-18T16:00:00Z',
    mount: 'platform',
    page: '/infra/legacy-proxy',
    ravn: 'ravn-skald',
    kind: 'lint-fix',
    message: 'auto-fixed orphan page — added backlink from /infra/k8s',
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
// Seed data — Activity event log
// ---------------------------------------------------------------------------

const MOCK_ACTIVITY_EVENTS: ActivityEvent[] = [
  {
    id: 'act-001',
    timestamp: '2026-04-19T10:14:02Z',
    kind: 'write',
    mount: 'local',
    ravn: 'ravn-fjolnir',
    message: 'rewrote compiled-truth of entities/person-karpathy.md',
    page: 'entities/person-karpathy.md',
  },
  {
    id: 'act-002',
    timestamp: '2026-04-19T10:12:40Z',
    kind: 'lint',
    mount: 'shared',
    ravn: 'ravn-skald',
    message: 'auto-fixed L11 (stale index.md, 3 entries added)',
    page: 'index.md',
  },
  {
    id: 'act-003',
    timestamp: '2026-04-19T10:10:18Z',
    kind: 'ingest',
    mount: 'local',
    ravn: 'ravn-fjolnir',
    message: 'ingested karpathy.github.io — pages_updated=1',
    page: 'entities/person-karpathy.md',
  },
  {
    id: 'act-004',
    timestamp: '2026-04-19T10:08:54Z',
    kind: 'write',
    mount: 'shared',
    ravn: 'ravn-fjolnir',
    message: 'appended timeline entry to entities/project-niuu.md',
    page: 'entities/project-niuu.md',
  },
  {
    id: 'act-005',
    timestamp: '2026-04-19T09:54:22Z',
    kind: 'dream',
    mount: 'shared',
    ravn: 'ravn-fjolnir',
    message: 'dream cycle complete (pages_updated=2, lint_fixes=1)',
  },
  {
    id: 'act-006',
    timestamp: '2026-04-19T09:44:01Z',
    kind: 'query',
    mount: 'shared',
    ravn: 'ravn-fjolnir',
    message: 'mimir_query "compounding knowledge" → 4 results',
  },
  {
    id: 'act-007',
    timestamp: '2026-04-19T09:41:12Z',
    kind: 'ingest',
    mount: 'platform',
    ravn: 'ravn-skald',
    message: 'ingested Anycubic resin datasheet',
    page: 'technical/forge/resin-profiles.md',
  },
  {
    id: 'act-008',
    timestamp: '2026-04-19T09:22:10Z',
    kind: 'lint',
    mount: 'platform',
    ravn: 'ravn-skald',
    message: 'L02 contradiction detected in resin-profiles (escalated)',
    page: 'technical/forge/resin-profiles.md',
  },
  {
    id: 'act-009',
    timestamp: '2026-04-19T09:11:08Z',
    kind: 'write',
    mount: 'local',
    ravn: 'ravn-fjolnir',
    message: 'wrote projects/niuu/architecture.md',
    page: 'projects/niuu/architecture.md',
  },
  {
    id: 'act-010',
    timestamp: '2026-04-19T08:58:44Z',
    kind: 'query',
    mount: 'local',
    ravn: 'ravn-fjolnir',
    message: 'mimir_query "cluster upgrade" → 2 results',
  },
];

// ---------------------------------------------------------------------------
// Mock adapter
// ---------------------------------------------------------------------------

export function createMimirMockAdapter(): IMimirService {
  // Mutable copies for write operations
  let lintIssues = [...INITIAL_LINT_ISSUES];
  let routingRules = [...INITIAL_ROUTING_RULES];
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

      async listRoutingRules(): Promise<WriteRoutingRule[]> {
        return [...routingRules].sort((a, b) => a.priority - b.priority);
      },

      async upsertRoutingRule(rule: WriteRoutingRule): Promise<WriteRoutingRule> {
        const idx = routingRules.findIndex((r) => r.id === rule.id);
        if (idx >= 0) {
          routingRules[idx] = rule;
        } else {
          routingRules = [...routingRules, rule];
        }
        return rule;
      },

      async deleteRoutingRule(id: string): Promise<void> {
        routingRules = routingRules.filter((r) => r.id !== id);
      },

      async listRavnBindings(): Promise<RavnBinding[]> {
        return MOCK_RAVN_BINDINGS;
      },

      async getRecentWrites(limit = 20): Promise<RecentWrite[]> {
        return MOCK_RECENT_WRITES.slice(0, limit);
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

      async getPage(path: string): Promise<Page | null> {
        return ALL_PAGES.find((p) => p.path === path) ?? null;
      },

      async upsertPage(): Promise<void> {
        // no-op in mock
      },

      async search(query: string): Promise<SearchResult[]> {
        const q = query.toLowerCase();
        return MOCK_PAGES.filter(
          (p) => p.title.toLowerCase().includes(q) || p.summary.toLowerCase().includes(q),
        ).map((p, i) => ({
          path: p.path,
          title: p.title,
          summary: p.summary,
          category: p.category,
          type: p.type,
          confidence: p.confidence,
          score: Math.max(0.5, 0.95 - i * 0.1),
          mounts: p.mounts,
        }));
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

      async getGraph(options): Promise<MimirGraph> {
        if (!options?.mountName) {
          return MOCK_GRAPH;
        }
        const mountPages = new Set(
          ALL_PAGES.filter((p) => p.mounts.includes(options.mountName!)).map((p) => p.path),
        );
        return {
          nodes: MOCK_GRAPH.nodes.filter((n) => mountPages.has(n.id)),
          edges: MOCK_GRAPH.edges.filter(
            (e) => mountPages.has(e.source) && mountPages.has(e.target),
          ),
        };
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

    lint: {
      async getLintReport(mountName?: string): Promise<LintReport> {
        const issues = mountName ? lintIssues.filter((i) => i.mount === mountName) : lintIssues;
        return {
          issues,
          pagesChecked: MOCK_PAGES.length,
          summary: tallySeverity(issues),
        };
      },

      async runAutoFix(issueIds?: string[]): Promise<LintReport> {
        if (issueIds) {
          lintIssues = lintIssues.filter((i) => !issueIds.includes(i.id) || !i.autoFix);
        } else {
          lintIssues = lintIssues.filter((i) => !i.autoFix);
        }
        return {
          issues: lintIssues,
          pagesChecked: MOCK_PAGES.length,
          summary: tallySeverity(lintIssues),
        };
      },

      async reassignIssues(issueIds: string[], assignee: string): Promise<LintReport> {
        lintIssues = lintIssues.map((i) => (issueIds.includes(i.id) ? { ...i, assignee } : i));
        return {
          issues: lintIssues,
          pagesChecked: MOCK_PAGES.length,
          summary: tallySeverity(lintIssues),
        };
      },

      async getDreamCycles(limit = 20): Promise<DreamCycle[]> {
        return MOCK_DREAM_CYCLES.slice(0, limit);
      },

      async getActivityLog(limit = 50): Promise<ActivityEvent[]> {
        return MOCK_ACTIVITY_EVENTS.slice(0, limit);
      },
    },
  };
}
