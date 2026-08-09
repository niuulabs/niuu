/**
 * Mock adapters for all Ting ports.
 *
 * Seeded with representative data mirroring the Ting backend's test fixtures.
 * Each factory returns an in-memory implementation suitable for tests and
 * local development without a running backend.
 */

import type {
  ITingService,
  IDispatcherService,
  ITingSessionService,
  ITrackerBrowserService,
  IWorkflowService,
  IResearchService,
  ISpecsService,
  WorkflowLaunchRequest,
  CreateResearchCampaignRequest,
  CreateSpecCampaignRequest,
  ReviewSpecCampaignRequest,
  UpdateResearchCampaignRequest,
  IDispatchBus,
  DispatchResult,
  ITingSettingsService,
  IAuditLogService,
  DispatcherActivityEvent,
  CommitSagaRequest,
  PlanSession,
  ExtractedStructure,
  RunSessionMessage,
  DispatchCluster,
  FlockConfig,
  DispatchDefaults,
  NotificationSettings,
  AuditEntry,
  AuditFilter,
  ResearchCampaign,
  ResearchCampaignDetail,
  CampaignArtifactDetail,
  ImportProjectOptions,
} from '../ports';
import type { Saga, Phase, Run } from '../domain/saga';
import type { DispatcherState } from '../domain/dispatcher';
import type { SessionInfo } from '../domain/session';
import type { TrackerProject, TrackerMilestone, TrackerIssue } from '../domain/tracker';
import type { Workflow } from '../domain/workflow';

// ---------------------------------------------------------------------------
// Seed helpers
// ---------------------------------------------------------------------------

let _runSeq = 0;
function mkRun(overrides: Partial<Run> & Pick<Run, 'id' | 'phaseId' | 'name' | 'status'>): Run {
  _runSeq++;
  // Varied defaults matching web2 prototype data
  const confidencePool = [92, 85, 78, 55, 72, 68, 44, 80, 61, 90];
  const estimatePool = [1, 0.25, 0.5, 2, 3, 1.5, 4, 0.5, 1, 2];
  const waitPool = [2, 0, 14, 48, 0, 6, 0, 22, 0, 3];
  const idx = (_runSeq - 1) % 10;
  const now = new Date();
  const updatedAt = new Date(now.getTime() - waitPool[idx]! * 60_000).toISOString();
  // Derive per-run tracker ID from parent saga's trackerId if not provided
  const parentTrackerId = overrides.trackerId || '';
  return {
    trackerId: parentTrackerId,
    description: '',
    acceptanceCriteria: [],
    declaredFiles: [],
    estimateHours: estimatePool[idx]!,
    confidence: confidencePool[idx]!,
    sessionId: null,
    reviewerSessionId: null,
    reviewRound: 0,
    branch: null,
    chronicleSummary: null,
    retryCount: 0,
    createdAt: '2026-01-15T00:00:00Z',
    updatedAt,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Seed data
// ---------------------------------------------------------------------------

const SEED_SAGAS: Saga[] = [
  // ── Dashboard-visible active sagas (web2 baseline) ──
  {
    id: '00000000-0000-0000-0000-000000000004',
    trackerId: 'NIU-214',
    trackerType: 'linear',
    slug: 'flokk-subscription-validation',
    name: 'Flokk subscription validation',
    repos: ['niuulabs/volundr'],
    featureBranch: 'feat/flokk-subs',
    baseBranch: 'main',
    status: 'active',
    confidence: 82,
    createdAt: '2026-01-20T05:00:00Z',
    phaseSummary: { total: 4, completed: 1 },
    workflow: 'ship',
    workflowVersion: '1.4.2',
  },
  {
    id: '00000000-0000-0000-0000-000000000005',
    trackerId: 'NIU-199',
    trackerType: 'linear',
    slug: 'observatory-canvas-realms',
    name: 'Observatory canvas realms overlay',
    repos: ['niuulabs/volundr'],
    featureBranch: 'feat/canvas-realms',
    baseBranch: 'main',
    status: 'active',
    confidence: 71,
    createdAt: '2026-01-19T05:00:00Z',
    phaseSummary: { total: 3, completed: 1 },
    workflow: 'ship',
    workflowVersion: '1.4.2',
  },
  {
    id: '00000000-0000-0000-0000-000000000006',
    trackerId: 'NIU-183',
    trackerType: 'linear',
    slug: 'mimir-chronicle-indexing',
    name: 'Mimir chronicle indexing pipeline',
    repos: ['niuulabs/volundr'],
    featureBranch: 'feat/chronicle-indexing',
    baseBranch: 'main',
    status: 'active',
    confidence: 68,
    createdAt: '2026-01-18T05:00:00Z',
    phaseSummary: { total: 3, completed: 2 },
    workflow: 'ship',
    workflowVersion: '1.4.2',
  },
  {
    id: '00000000-0000-0000-0000-000000000007',
    trackerId: 'NIU-148',
    trackerType: 'linear',
    slug: 'bifrost-rate-limit',
    name: 'Bifröst rate-limit per-model',
    repos: ['niuulabs/volundr'],
    featureBranch: 'feat/rate-limit',
    baseBranch: 'main',
    status: 'active',
    confidence: 31,
    createdAt: '2026-01-17T05:00:00Z',
    phaseSummary: { total: 2, completed: 2 },
    workflow: 'ship',
    workflowVersion: '1.4.2',
  },
  // ── Original sagas (Auth Rewrite now complete) ──
  {
    id: '00000000-0000-0000-0000-000000000001',
    trackerId: 'NIU-500',
    trackerType: 'linear',
    slug: 'auth-rewrite',
    name: 'Auth Rewrite',
    repos: ['niuulabs/volundr'],
    featureBranch: 'feat/auth-rewrite',
    baseBranch: 'main',
    status: 'complete',
    confidence: 82,
    createdAt: '2026-01-10T09:00:00Z',
    phaseSummary: { total: 3, completed: 1 },
    workflow: 'ship',
    workflowVersion: '1.4.2',
  },
  {
    id: '00000000-0000-0000-0000-000000000002',
    trackerId: 'NIU-520',
    trackerType: 'linear',
    slug: 'plugin-ravn',
    name: 'Plugin Ravn Scaffold',
    repos: ['niuulabs/volundr'],
    featureBranch: 'feat/plugin-ravn',
    baseBranch: 'main',
    status: 'complete',
    confidence: 95,
    createdAt: '2026-01-05T08:00:00Z',
    phaseSummary: { total: 2, completed: 2 },
    workflow: 'scaffold',
    workflowVersion: '2.1.0',
  },
  {
    id: '00000000-0000-0000-0000-000000000003',
    trackerId: 'NIU-600',
    trackerType: 'linear',
    slug: 'observatory-topology',
    name: 'Observatory Topology Canvas',
    repos: ['niuulabs/volundr'],
    featureBranch: 'feat/topology-canvas',
    baseBranch: 'main',
    status: 'failed',
    confidence: 30,
    createdAt: '2026-01-15T10:00:00Z',
    phaseSummary: { total: 4, completed: 0 },
    workflow: 'ship',
    workflowVersion: '1.3.0',
  },
];

const SEED_RUNS: Run[] = [
  {
    id: '00000000-0000-0000-0000-000000000010',
    phaseId: '00000000-0000-0000-0000-000000000100',
    trackerId: 'NIU-501',
    name: 'Implement OIDC flow',
    description: 'Add OIDC login via Keycloak.',
    acceptanceCriteria: ['Users can log in with SSO', 'Token refreshes silently'],
    declaredFiles: ['src/auth/oidc.ts', 'src/auth/refresh.ts'],
    estimateHours: 8,
    status: 'merged',
    confidence: 90,
    sessionId: 'sess-001',
    reviewerSessionId: null,
    reviewRound: 1,
    branch: 'feat/auth-rewrite',
    chronicleSummary: 'Implemented OIDC flow with silent refresh.',
    retryCount: 0,
    createdAt: '2026-01-10T09:00:00Z',
    updatedAt: '2026-01-12T14:00:00Z',
  },
  {
    id: '00000000-0000-0000-0000-000000000011',
    phaseId: '00000000-0000-0000-0000-000000000101',
    trackerId: 'NIU-502',
    name: 'Add PAT generation',
    description: 'Personal access tokens for headless dispatch.',
    acceptanceCriteria: ['PATs can be created and revoked', 'Envoy validates PATs'],
    declaredFiles: ['src/niuu/pat.ts'],
    estimateHours: 4,
    status: 'merged',
    confidence: 65,
    sessionId: 'sess-002',
    reviewerSessionId: null,
    reviewRound: 0,
    branch: 'feat/auth-rewrite',
    chronicleSummary: null,
    retryCount: 0,
    createdAt: '2026-01-13T09:00:00Z',
    updatedAt: '2026-01-13T11:00:00Z',
  },
  // Dispatch-queue seed data — runs in dispatchable states
  {
    id: '00000000-0000-0000-0000-000000000012',
    phaseId: '00000000-0000-0000-0000-000000000102',
    trackerId: 'NIU-503',
    name: 'Harden JWT validation',
    description: 'Add clock-skew tolerance and token audience checks.',
    acceptanceCriteria: ['JWT rejects tampered tokens', 'Clock skew ≤ 60s tolerated'],
    declaredFiles: ['src/auth/jwt.ts'],
    estimateHours: 3,
    status: 'pending',
    confidence: 80,
    sessionId: null,
    reviewerSessionId: null,
    reviewRound: 0,
    branch: null,
    chronicleSummary: null,
    retryCount: 0,
    createdAt: '2026-01-14T08:00:00Z',
    updatedAt: '2026-01-14T08:00:00Z',
  },
  {
    id: '00000000-0000-0000-0000-000000000013',
    phaseId: '00000000-0000-0000-0000-000000000102',
    trackerId: 'NIU-504',
    name: 'Write auth integration tests',
    description: 'End-to-end tests for the full auth flow.',
    acceptanceCriteria: ['All auth happy paths covered', 'Token expiry tested'],
    declaredFiles: ['tests/auth/integration.test.ts'],
    estimateHours: 5,
    status: 'pending',
    confidence: 45,
    sessionId: null,
    reviewerSessionId: null,
    reviewRound: 0,
    branch: null,
    chronicleSummary: null,
    retryCount: 0,
    createdAt: '2026-01-14T09:00:00Z',
    updatedAt: '2026-01-14T09:00:00Z',
  },
  {
    id: '00000000-0000-0000-0000-000000000014',
    phaseId: '00000000-0000-0000-0000-000000000102',
    trackerId: 'NIU-505',
    name: 'Add refresh token rotation',
    description: 'Rotate refresh tokens on every use.',
    acceptanceCriteria: ['Old refresh tokens invalidated on use', 'New token issued atomically'],
    declaredFiles: ['src/auth/refresh.ts'],
    estimateHours: 4,
    status: 'queued',
    confidence: 75,
    sessionId: null,
    reviewerSessionId: null,
    reviewRound: 0,
    branch: null,
    chronicleSummary: null,
    retryCount: 0,
    createdAt: '2026-01-15T10:00:00Z',
    updatedAt: '2026-01-15T10:30:00Z',
  },
];

const SEED_PHASES: Phase[] = [
  {
    id: '00000000-0000-0000-0000-000000000100',
    sagaId: '00000000-0000-0000-0000-000000000001',
    trackerId: 'NIU-M1',
    number: 1,
    name: 'Phase 1: Foundation',
    status: 'complete',
    confidence: 90,
    runs: [SEED_RUNS[0]!],
  },
  {
    id: '00000000-0000-0000-0000-000000000101',
    sagaId: '00000000-0000-0000-0000-000000000001',
    trackerId: 'NIU-M2',
    number: 2,
    name: 'Phase 2: PAT Support',
    status: 'complete',
    confidence: 65,
    runs: [SEED_RUNS[1]!],
  },
  {
    id: '00000000-0000-0000-0000-000000000102',
    sagaId: '00000000-0000-0000-0000-000000000001',
    trackerId: 'NIU-M3',
    number: 3,
    name: 'Phase 3: Security',
    status: 'pending',
    confidence: 50,
    runs: [SEED_RUNS[2]!, SEED_RUNS[3]!, SEED_RUNS[4]!],
  },
];

// ── Dashboard saga phases (web2 baseline data) ──────────────────────────────

const SEED_FLOKK_PHASES: Phase[] = [
  {
    id: '00000000-0000-0000-0000-000000000200',
    sagaId: '00000000-0000-0000-0000-000000000004',
    trackerId: 'NIU-M4',
    number: 1,
    name: 'Phase 1: Setup',
    status: 'complete',
    confidence: 90,
    runs: [
      mkRun({
        id: '00000000-0000-0000-0000-000000000020',
        phaseId: '00000000-0000-0000-0000-000000000200',
        name: 'Bootstrap Flokk schema',
        status: 'merged',
      }),
      mkRun({
        id: '00000000-0000-0000-0000-000000000021',
        phaseId: '00000000-0000-0000-0000-000000000200',
        name: 'Subscription port',
        status: 'merged',
      }),
    ],
  },
  {
    id: '00000000-0000-0000-0000-000000000201',
    sagaId: '00000000-0000-0000-0000-000000000004',
    trackerId: 'NIU-M5',
    number: 2,
    name: 'Phase 2: Validation',
    status: 'active',
    confidence: 72,
    runs: [
      mkRun({
        id: '00000000-0000-0000-0000-000000000022',
        phaseId: '00000000-0000-0000-0000-000000000201',
        trackerId: 'NIU-214.4',
        name: 'Integration tests for graph validator',
        status: 'running',
        estimateHours: 1,
        confidence: 92,
      }),
    ],
  },
  {
    id: '00000000-0000-0000-0000-000000000202',
    sagaId: '00000000-0000-0000-0000-000000000004',
    trackerId: 'NIU-M6',
    number: 3,
    name: 'Phase 3: Webhooks',
    status: 'pending',
    confidence: 50,
    runs: [
      mkRun({
        id: '00000000-0000-0000-0000-000000000023',
        phaseId: '00000000-0000-0000-0000-000000000202',
        trackerId: 'NIU-214.5',
        name: 'Release cut',
        status: 'pending',
        estimateHours: 0.25,
        confidence: 55,
      }),
    ],
  },
  {
    id: '00000000-0000-0000-0000-000000000203',
    sagaId: '00000000-0000-0000-0000-000000000004',
    trackerId: 'NIU-M7',
    number: 4,
    name: 'Phase 4: Metrics',
    status: 'pending',
    confidence: 40,
    runs: [
      mkRun({
        id: '00000000-0000-0000-0000-000000000024',
        phaseId: '00000000-0000-0000-0000-000000000203',
        trackerId: 'NIU-214.6',
        name: 'Subscription metrics',
        status: 'pending',
        estimateHours: 2,
        confidence: 68,
      }),
    ],
  },
];

const SEED_OBSERVATORY_PHASES: Phase[] = [
  {
    id: '00000000-0000-0000-0000-000000000210',
    sagaId: '00000000-0000-0000-0000-000000000005',
    trackerId: 'NIU-M8',
    number: 1,
    name: 'Phase 1: Canvas',
    status: 'complete',
    confidence: 88,
    runs: [
      mkRun({
        id: '00000000-0000-0000-0000-000000000030',
        phaseId: '00000000-0000-0000-0000-000000000210',
        name: 'Canvas renderer',
        status: 'merged',
      }),
      mkRun({
        id: '00000000-0000-0000-0000-000000000031',
        phaseId: '00000000-0000-0000-0000-000000000210',
        name: 'Realm boundaries',
        status: 'merged',
      }),
      mkRun({
        id: '00000000-0000-0000-0000-000000000032',
        phaseId: '00000000-0000-0000-0000-000000000210',
        name: 'Node layout',
        status: 'merged',
      }),
    ],
  },
  {
    id: '00000000-0000-0000-0000-000000000211',
    sagaId: '00000000-0000-0000-0000-000000000005',
    trackerId: 'NIU-M9',
    number: 2,
    name: 'Phase 2: Overlays',
    status: 'active',
    confidence: 60,
    runs: [
      mkRun({
        id: '00000000-0000-0000-0000-000000000033',
        phaseId: '00000000-0000-0000-0000-000000000211',
        trackerId: 'NIU-199.3',
        name: 'Realm colour ramp tokens',
        status: 'running',
        estimateHours: 1,
        confidence: 85,
      }),
      mkRun({
        id: '00000000-0000-0000-0000-000000000034',
        phaseId: '00000000-0000-0000-0000-000000000211',
        trackerId: 'NIU-199.4',
        name: 'Review arbitration',
        status: 'escalated',
        estimateHours: 0.5,
        confidence: 44,
      }),
    ],
  },
  {
    id: '00000000-0000-0000-0000-000000000212',
    sagaId: '00000000-0000-0000-0000-000000000005',
    trackerId: 'NIU-M10',
    number: 3,
    name: 'Phase 3: Interaction',
    status: 'pending',
    confidence: 40,
    runs: [
      mkRun({
        id: '00000000-0000-0000-0000-000000000035',
        phaseId: '00000000-0000-0000-0000-000000000212',
        trackerId: 'NIU-199.5',
        name: 'Click-to-select',
        status: 'pending',
        estimateHours: 1.5,
        confidence: 72,
      }),
    ],
  },
];

const SEED_MIMIR_PHASES: Phase[] = [
  {
    id: '00000000-0000-0000-0000-000000000220',
    sagaId: '00000000-0000-0000-0000-000000000006',
    trackerId: 'NIU-M11',
    number: 1,
    name: 'Phase 1: Schema',
    status: 'complete',
    confidence: 95,
    runs: [
      mkRun({
        id: '00000000-0000-0000-0000-000000000040',
        phaseId: '00000000-0000-0000-0000-000000000220',
        name: 'Chronicle schema',
        status: 'merged',
      }),
      mkRun({
        id: '00000000-0000-0000-0000-000000000041',
        phaseId: '00000000-0000-0000-0000-000000000220',
        name: 'Index strategy',
        status: 'merged',
      }),
    ],
  },
  {
    id: '00000000-0000-0000-0000-000000000221',
    sagaId: '00000000-0000-0000-0000-000000000006',
    trackerId: 'NIU-M12',
    number: 2,
    name: 'Phase 2: Ingestion',
    status: 'complete',
    confidence: 85,
    runs: [
      mkRun({
        id: '00000000-0000-0000-0000-000000000042',
        phaseId: '00000000-0000-0000-0000-000000000221',
        name: 'Ingest pipeline',
        status: 'merged',
      }),
      mkRun({
        id: '00000000-0000-0000-0000-000000000043',
        phaseId: '00000000-0000-0000-0000-000000000221',
        name: 'Batch writer',
        status: 'merged',
      }),
    ],
  },
  {
    id: '00000000-0000-0000-0000-000000000222',
    sagaId: '00000000-0000-0000-0000-000000000006',
    trackerId: 'NIU-M13',
    number: 3,
    name: 'Phase 3: Query',
    status: 'active',
    confidence: 55,
    runs: [
      mkRun({
        id: '00000000-0000-0000-0000-000000000044',
        phaseId: '00000000-0000-0000-0000-000000000222',
        trackerId: 'NIU-183.4',
        name: 'Arbitrated review',
        status: 'review',
        estimateHours: 1,
        confidence: 55,
        retryCount: 1,
      }),
      mkRun({
        id: '00000000-0000-0000-0000-000000000045',
        phaseId: '00000000-0000-0000-0000-000000000222',
        trackerId: 'NIU-183.5',
        name: 'Result ranking',
        status: 'escalated',
        estimateHours: 2,
        confidence: 38,
      }),
    ],
  },
];

const SEED_BIFROST_PHASES: Phase[] = [
  {
    id: '00000000-0000-0000-0000-000000000230',
    sagaId: '00000000-0000-0000-0000-000000000007',
    trackerId: 'NIU-M14',
    number: 1,
    name: 'Phase 1: Limiter',
    status: 'complete',
    confidence: 90,
    runs: [
      mkRun({
        id: '00000000-0000-0000-0000-000000000050',
        phaseId: '00000000-0000-0000-0000-000000000230',
        name: 'Token bucket limiter',
        status: 'merged',
      }),
    ],
  },
  {
    id: '00000000-0000-0000-0000-000000000231',
    sagaId: '00000000-0000-0000-0000-000000000007',
    trackerId: 'NIU-M15',
    number: 2,
    name: 'Phase 2: Per-model',
    status: 'complete',
    confidence: 25,
    runs: [
      mkRun({
        id: '00000000-0000-0000-0000-000000000051',
        phaseId: '00000000-0000-0000-0000-000000000231',
        name: 'Model quota config',
        status: 'review',
      }),
      mkRun({
        id: '00000000-0000-0000-0000-000000000052',
        phaseId: '00000000-0000-0000-0000-000000000231',
        name: 'Quota enforcement',
        status: 'failed',
      }),
    ],
  },
];

const SEED_DISPATCHER_STATE: DispatcherState = {
  id: '00000000-0000-0000-0000-000000000999',
  running: true,
  threshold: 70,
  maxConcurrentRuns: 5,
  autoContinue: false,
  updatedAt: '2026-01-13T11:00:00Z',
};

const SEED_SESSIONS: SessionInfo[] = [
  {
    sessionId: 'sess-001',
    status: 'complete',
    chronicleLines: [
      '[10:00] Starting OIDC implementation',
      '[10:45] Created oidc.ts with PKCE flow',
      '[11:30] All acceptance tests pass',
    ],
    branch: 'feat/auth-rewrite',
    confidence: 90,
    runName: 'Implement OIDC flow',
    sagaName: 'Auth Rewrite',
    clusterName: 'Mac mini',
  },
  {
    sessionId: 'sess-002',
    status: 'running',
    chronicleLines: ['[09:00] Starting PAT generation', '[09:30] JWT signing implemented'],
    branch: 'feat/auth-rewrite',
    confidence: 65,
    runName: 'Add PAT generation',
    sagaName: 'Auth Rewrite',
    clusterName: 'MacBook Pro',
  },
];

const SEED_DISPATCH_CLUSTERS: DispatchCluster[] = [
  {
    connectionId: 'cluster-mini',
    name: 'Mac mini',
    url: 'http://mac-mini.local:8000',
    enabled: true,
  },
  {
    connectionId: 'cluster-macbook',
    name: 'MacBook Pro',
    url: 'http://macbook-pro.local:8000',
    enabled: true,
  },
  {
    connectionId: 'cluster-macbook-air',
    name: 'MacBook Air',
    url: 'http://macbook-air.local:8000',
    enabled: true,
  },
];

const SEED_PROJECTS: TrackerProject[] = [
  {
    id: 'proj-niuu-core',
    name: 'Niuu Core',
    description: 'Core platform features',
    status: 'active',
    url: 'https://linear.app/niuu/proj/niuu-core',
    milestoneCount: 8,
    issueCount: 64,
    slug: 'niuu-core',
  },
  {
    id: 'proj-plugin-platform',
    name: 'Plugin Platform',
    description: 'Composable plugin infrastructure',
    status: 'active',
    url: 'https://linear.app/niuu/proj/plugin-platform',
    milestoneCount: 4,
    issueCount: 28,
    slug: 'plugin-platform',
  },
];

const SEED_MILESTONES: TrackerMilestone[] = [
  {
    id: 'ms-auth',
    projectId: 'proj-niuu-core',
    name: 'Auth & Identity',
    description: 'OIDC, PATs, and session management',
    sortOrder: 1,
    progress: 60,
  },
  {
    id: 'ms-observatory',
    projectId: 'proj-niuu-core',
    name: 'Observatory',
    description: 'Topology, registry, events',
    sortOrder: 2,
    progress: 80,
  },
];

const SEED_ISSUES: TrackerIssue[] = [
  {
    id: 'iss-679',
    identifier: 'NIU-679',
    title: '@niuulabs/plugin-ting scaffold',
    description: 'Scaffold Ting plugin with domain, ports, mock, HTTP adapters',
    status: 'in_progress',
    assignee: null,
    labels: ['plugin', 'scaffold'],
    priority: 1,
    url: 'https://linear.app/niuu/issue/NIU-679',
    milestoneId: 'ms-auth',
  },
  {
    id: 'iss-671',
    identifier: 'NIU-671',
    title: '@niuulabs/plugin-ravn scaffold',
    description: 'Scaffold Ravn plugin',
    status: 'done',
    assignee: null,
    labels: ['plugin', 'scaffold'],
    priority: 1,
    url: 'https://linear.app/niuu/issue/NIU-671',
    milestoneId: null,
  },
];

const SEED_WORKFLOWS: Workflow[] = [
  {
    id: '00000000-0000-0000-0000-000000000a01',
    name: 'ship — default release cycle',
    version: '1.4.2',
    description:
      'qa → pre-ship review → version bump → release PR. Matches src/ting/templates/ship.yaml.',
    tags: ['delivery', 'release'],
    nodes: [
      {
        id: 'stage-dispatch',
        kind: 'stage',
        label: 'manual dispatch',
        runId: '00000000-0000-0000-0000-000000000010',
        personaIds: ['persona-qa'],
        position: { x: 36, y: 236 },
      },
      {
        id: 'stage-test',
        kind: 'stage',
        label: 'test-suite',
        runId: '00000000-0000-0000-0000-000000000010',
        personaIds: ['persona-qa'],
        position: { x: 248, y: 132 },
      },
      {
        id: 'stage-review',
        kind: 'stage',
        label: 'pre-ship-review',
        runId: '00000000-0000-0000-0000-000000000011',
        personaIds: ['persona-reviewer'],
        position: { x: 484, y: 132 },
      },
      {
        id: 'cond-verdict',
        kind: 'cond',
        label: 'review.verdict',
        predicate: 'review.verdict === "pass"',
        position: { x: 560, y: 18 },
      },
      {
        id: 'stage-version',
        kind: 'stage',
        label: 'version-bump',
        runId: null,
        personaIds: ['persona-coder'],
        position: { x: 744, y: 132 },
      },
      {
        id: 'stage-release',
        kind: 'stage',
        label: 'release-pr',
        runId: null,
        personaIds: ['persona-gatekeeper'],
        position: { x: 744, y: 260 },
      },
    ],
    edges: [
      {
        id: 'e1',
        source: 'stage-dispatch',
        target: 'stage-test',
        cp1: { x: 80, y: 0 },
        cp2: { x: -80, y: 0 },
      },
      {
        id: 'e2',
        source: 'stage-test',
        target: 'stage-review',
        cp1: { x: 80, y: 0 },
        cp2: { x: -80, y: 0 },
      },
      {
        id: 'e3',
        source: 'stage-review',
        target: 'stage-dispatch',
        cp1: { x: -60, y: 40 },
        cp2: { x: 60, y: 40 },
      },
      {
        id: 'e4',
        source: 'stage-review',
        target: 'cond-verdict',
        cp1: { x: 40, y: -40 },
        cp2: { x: -40, y: 20 },
      },
      {
        id: 'e5',
        source: 'cond-verdict',
        target: 'stage-version',
        label: 'pass',
        cp1: { x: 60, y: 30 },
        cp2: { x: -60, y: -30 },
      },
      {
        id: 'e5b',
        source: 'stage-version',
        target: 'stage-release',
        cp1: { x: 80, y: 30 },
        cp2: { x: -80, y: -20 },
      },
      {
        id: 'e6',
        source: 'stage-dispatch',
        target: 'cond-verdict',
        cp1: { x: 80, y: -60 },
        cp2: { x: -80, y: -20 },
      },
      {
        id: 'e7',
        source: 'stage-review',
        target: 'stage-release',
        cp1: { x: 80, y: 40 },
        cp2: { x: -80, y: -20 },
      },
    ],
  },
  {
    id: '00000000-0000-0000-0000-000000000a02',
    name: 'deep-review — code audit',
    version: '0.9.1',
    description: 'Full code audit pipeline with multiple reviewers and automated checks.',
    tags: ['review', 'audit'],
    nodes: [
      {
        id: 'stage-scan',
        kind: 'stage',
        label: 'Scaffold plugin',
        runId: null,
        personaIds: ['persona-plan'],
        position: { x: 100, y: 100 },
      },
      {
        id: 'stage-audit',
        kind: 'stage',
        label: 'Implement ports',
        runId: null,
        personaIds: [],
        position: { x: 380, y: 100 },
      },
    ],
    edges: [
      {
        id: 'e1',
        source: 'stage-scan',
        target: 'stage-audit',
        cp1: { x: 80, y: 0 },
        cp2: { x: -80, y: 0 },
      },
    ],
  },
  {
    id: '00000000-0000-0000-0000-000000000a03',
    name: 'investigate — triage & fix',
    version: '0.3.0',
    description: 'Investigation pipeline for triaging issues and applying targeted fixes.',
    nodes: [
      {
        id: 'stage-triage',
        kind: 'stage',
        label: 'Triage',
        runId: null,
        personaIds: ['persona-investigator'],
        position: { x: 100, y: 100 },
      },
      {
        id: 'stage-fix',
        kind: 'stage',
        label: 'Apply fix',
        runId: null,
        personaIds: ['persona-coder'],
        position: { x: 380, y: 100 },
      },
    ],
    edges: [
      {
        id: 'e1',
        source: 'stage-triage',
        target: 'stage-fix',
        cp1: { x: 80, y: 0 },
        cp2: { x: -80, y: 0 },
      },
    ],
  },
];

const SEED_FLOCK_CONFIG: FlockConfig = {
  flockName: 'Niuu Core',
  defaultBaseBranch: 'main',
  defaultTrackerType: 'linear',
  defaultRepos: ['niuulabs/volundr'],
  maxActiveSagas: 5,
  autoCreateMilestones: true,
  updatedAt: '2026-01-01T00:00:00Z',
};

const SEED_DISPATCH_DEFAULTS: DispatchDefaults = {
  confidenceThreshold: 70,
  maxConcurrentRuns: 3,
  autoContinue: false,
  batchSize: 10,
  retryPolicy: {
    maxRetries: 2,
    retryDelaySeconds: 30,
    escalateOnExhaustion: true,
  },
  quietHours: '22:00–07:00 UTC',
  escalateAfter: '30m',
  updatedAt: '2026-01-01T00:00:00Z',
};

const SEED_NOTIFICATION_SETTINGS: NotificationSettings = {
  channel: 'telegram',
  onRunPendingApproval: true,
  onRunMerged: false,
  onRunFailed: true,
  onSagaComplete: true,
  onDispatcherError: true,
  webhookUrl: null,
  updatedAt: '2026-01-01T00:00:00Z',
};

const SEED_AUDIT_ENTRIES: AuditEntry[] = [
  {
    id: '00000000-0000-0000-0000-000000000a01',
    kind: 'settings.flock_config.updated',
    summary: 'Updated flock name to "Niuu Core"',
    actor: 'user-1',
    payload: { before: { flockName: 'Old Name' }, after: { flockName: 'Niuu Core' } },
    createdAt: '2026-01-10T08:00:00Z',
  },
  {
    id: '00000000-0000-0000-0000-000000000a02',
    kind: 'dispatcher.started',
    summary: 'Dispatcher started',
    actor: 'system',
    payload: null,
    createdAt: '2026-01-10T09:00:00Z',
  },
  {
    id: '00000000-0000-0000-0000-000000000a03',
    kind: 'run.dispatched',
    summary: 'Run "Implement OIDC flow" dispatched (confidence: 90)',
    actor: 'dispatcher',
    payload: { runId: '00000000-0000-0000-0000-000000000010', confidence: 90 },
    createdAt: '2026-01-10T09:05:00Z',
  },
  {
    id: '00000000-0000-0000-0000-000000000a04',
    kind: 'run.merged',
    summary: 'Run "Implement OIDC flow" merged',
    actor: 'dispatcher',
    payload: { runId: '00000000-0000-0000-0000-000000000010' },
    createdAt: '2026-01-12T14:00:00Z',
  },
  {
    id: '00000000-0000-0000-0000-000000000a05',
    kind: 'settings.dispatch_defaults.updated',
    summary: 'Confidence threshold changed from 65 to 70',
    actor: 'user-1',
    payload: { before: { confidenceThreshold: 65 }, after: { confidenceThreshold: 70 } },
    createdAt: '2026-01-13T10:00:00Z',
  },
  {
    id: '00000000-0000-0000-0000-000000000a06',
    kind: 'dispatcher.threshold_changed',
    summary: 'Dispatch threshold set to 70',
    actor: 'user-1',
    payload: { threshold: 70 },
    createdAt: '2026-01-13T10:01:00Z',
  },
];

// ---------------------------------------------------------------------------
// Mock factories
// ---------------------------------------------------------------------------

/**
 * Create an in-memory ITingService backed by seed data.
 */
export function createMockTingService(): ITingService {
  const sagas = new Map<string, Saga>(SEED_SAGAS.map((s) => [s.id, s]));
  const phasesBySaga = new Map<string, Phase[]>([
    ['00000000-0000-0000-0000-000000000004', SEED_FLOKK_PHASES],
    ['00000000-0000-0000-0000-000000000005', SEED_OBSERVATORY_PHASES],
    ['00000000-0000-0000-0000-000000000006', SEED_MIMIR_PHASES],
    ['00000000-0000-0000-0000-000000000007', SEED_BIFROST_PHASES],
    ['00000000-0000-0000-0000-000000000001', [...SEED_PHASES]],
  ]);
  const messagesByRun = new Map<string, RunSessionMessage[]>();

  return {
    async getSagas() {
      return Array.from(sagas.values());
    },

    async getSaga(id: string) {
      return sagas.get(id) ?? null;
    },

    async deleteSaga(id: string) {
      sagas.delete(id);
    },

    async getPhases(sagaId: string) {
      return phasesBySaga.get(sagaId) ?? [];
    },

    async listRunMessages(runId: string) {
      return messagesByRun.get(runId) ?? [];
    },

    async sendRunMessage(runId: string, content: string, _targetPeerId?: string) {
      const run =
        Array.from(phasesBySaga.values())
          .flatMap((phases) => phases.flatMap((phase) => phase.runs))
          .find((entry) => entry.id === runId) ?? null;
      const message: RunSessionMessage = {
        id: `message-${Date.now()}`,
        sessionId: run?.sessionId ?? 'mock-session',
        content,
        sender: 'user',
        createdAt: new Date().toISOString(),
        kind: 'message',
        helpRequest: null,
      };
      messagesByRun.set(runId, [...(messagesByRun.get(runId) ?? []), message]);
      return message;
    },

    async createSaga(spec: string, _repo: string) {
      const saga: Saga = {
        id: `mock-${Date.now()}`,
        trackerId: '',
        trackerType: 'mock',
        slug: spec.slice(0, 20).toLowerCase().replace(/\s+/g, '-'),
        name: spec,
        repos: [_repo],
        featureBranch: `feat/${spec.slice(0, 20).toLowerCase().replace(/\s+/g, '-')}`,
        baseBranch: 'main',
        status: 'active',
        confidence: 50,
        createdAt: new Date().toISOString(),
        phaseSummary: { total: 0, completed: 0 },
      };
      sagas.set(saga.id, saga);
      return saga;
    },

    async commitSaga(request: CommitSagaRequest) {
      const saga: Saga = {
        id: `mock-${Date.now()}`,
        trackerId: '',
        trackerType: 'mock',
        slug: request.slug,
        name: request.name,
        repos: request.repos,
        featureBranch: `feat/${request.slug}`,
        baseBranch: 'main',
        status: 'active',
        confidence: 60,
        createdAt: new Date().toISOString(),
        phaseSummary: { total: request.phases.length, completed: 0 },
      };
      sagas.set(saga.id, saga);
      return saga;
    },

    async decompose(_spec: string, _repo: string) {
      // Small delay so the running step is visible in E2E tests
      await new Promise<void>((r) => setTimeout(r, 500));
      return [
        {
          id: 'phase-mock-1',
          sagaId: '',
          trackerId: '',
          number: 1,
          name: 'Phase 1: Foundation',
          status: 'pending' as const,
          confidence: 82,
          runs: [
            {
              id: 'run-mock-1',
              phaseId: 'phase-mock-1',
              trackerId: '',
              name: 'Scaffold core domain models',
              description: 'Define shared domain types and port interfaces.',
              acceptanceCriteria: ['All types exported from index.ts', 'No circular imports'],
              declaredFiles: ['src/domain/models.ts', 'src/ports/index.ts'],
              estimateHours: 4,
              status: 'pending' as const,
              confidence: 85,
              sessionId: null,
              reviewerSessionId: null,
              reviewRound: 0,
              branch: null,
              chronicleSummary: null,
              retryCount: 0,
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
            },
          ],
        },
        {
          id: 'phase-mock-2',
          sagaId: '',
          trackerId: '',
          number: 2,
          name: 'Phase 2: API layer',
          status: 'pending' as const,
          confidence: 75,
          runs: [
            {
              id: 'run-mock-2',
              phaseId: 'phase-mock-2',
              trackerId: '',
              name: 'Implement HTTP adapters',
              description: 'Build the REST API adapters for all service ports.',
              acceptanceCriteria: ['All endpoints covered', 'Snake_case ↔ camelCase transform'],
              declaredFiles: ['src/adapters/http.ts'],
              estimateHours: 8,
              status: 'pending' as const,
              confidence: 78,
              sessionId: null,
              reviewerSessionId: null,
              reviewRound: 0,
              branch: null,
              chronicleSummary: null,
              retryCount: 0,
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
            },
          ],
        },
      ];
    },

    async spawnPlanSession(_spec: string, _repo: string): Promise<PlanSession> {
      return {
        sessionId: `plan-${Date.now()}`,
        chatEndpoint: null,
        questions: [
          {
            id: 'q1',
            question: 'Which target repositories should this saga operate on?',
            hint: 'e.g. niuulabs/volundr, niuulabs/ting',
          },
          {
            id: 'q2',
            question: 'What is the base branch agents should branch off from?',
            hint: 'e.g. main, dev, feat/...',
          },
          {
            id: 'q3',
            question:
              'Are there any acceptance criteria or constraints you want enforced across all runs?',
            hint: 'e.g. all endpoints must have OpenAPI docs, no breaking API changes',
          },
          {
            id: 'q4',
            question:
              'What is the desired confidence threshold before the dispatcher auto-continues?',
            hint: 'e.g. 80 — runs below this will pause for operator approval',
          },
        ],
      };
    },

    async listPlanSessions(): Promise<PlanSession[]> {
      return [];
    },

    async cancelPlanSession(_campaignSlug: string): Promise<void> {
      return undefined;
    },

    async extractStructure(_text: string): Promise<ExtractedStructure> {
      return {
        found: true,
        structure: {
          name: 'New Saga',
          phases: [
            {
              name: 'Phase 1: Foundation',
              runs: [
                {
                  name: 'Scaffold core domain models',
                  description: 'Define shared domain types and port interfaces.',
                  acceptanceCriteria: ['All types exported from index.ts', 'No circular imports'],
                  declaredFiles: ['src/domain/models.ts', 'src/ports/index.ts'],
                  estimateHours: 4,
                  confidence: 85,
                },
              ],
            },
            {
              name: 'Phase 2: API layer',
              runs: [
                {
                  name: 'Implement HTTP adapters',
                  description: 'Build the REST API adapters for all service ports.',
                  acceptanceCriteria: ['All endpoints covered', 'Snake_case ↔ camelCase transform'],
                  declaredFiles: ['src/adapters/http.ts'],
                  estimateHours: 8,
                  confidence: 78,
                },
              ],
            },
          ],
        },
      };
    },

    async assignWorkflow(sagaId: string, workflowId: string | null) {
      const saga = sagas.get(sagaId);
      if (!saga) {
        throw new Error(`Saga not found: ${sagaId}`);
      }

      const updated: Saga = {
        ...saga,
        workflowId: workflowId ?? undefined,
        workflow: workflowId ? 'custom-workflow' : undefined,
        workflowVersion: workflowId ? '1.0.0' : undefined,
      };
      sagas.set(sagaId, updated);
      return updated;
    },

    async assignTarget(sagaId: string, target) {
      const saga = sagas.get(sagaId);
      if (!saga) {
        throw new Error(`Saga not found: ${sagaId}`);
      }

      const updated: Saga = {
        ...saga,
        instanceId: target.mode === 'instance' ? target.instanceId : undefined,
        instanceName: target.mode === 'instance' ? 'Assigned target' : undefined,
        targetTags: target.mode === 'tags' ? target.tags : undefined,
        targetMatch: target.mode === 'tags' ? (target.match ?? 'all') : undefined,
      };
      sagas.set(sagaId, updated);
      return updated;
    },

    async assignRepos(sagaId: string, repoRefs) {
      const saga = sagas.get(sagaId);
      if (!saga) {
        throw new Error(`Saga not found: ${sagaId}`);
      }

      const updated: Saga = {
        ...saga,
        repos: repoRefs.map((entry) => entry.repo),
        repoRefs,
        baseBranch: repoRefs[0]?.branch ?? saga.baseBranch,
      };
      sagas.set(sagaId, updated);
      return updated;
    },
  };
}

/**
 * Create an in-memory IDispatcherService.
 */
export function createMockDispatcherService(): IDispatcherService {
  let state: DispatcherState = { ...SEED_DISPATCHER_STATE };
  const log: string[] = ['[mock] Dispatcher initialized'];
  const activityLog: DispatcherActivityEvent[] = [
    {
      id: 'evt-1',
      event: 'run.state_changed',
      ownerId: 'dev-user',
      timestamp: '2026-01-20T14:04:00Z',
      data: {
        tracker_id: 'NIU-214.2',
        status: 'running',
        action: 'dispatch',
      },
    },
    {
      id: 'evt-2',
      event: 'confidence.updated',
      ownerId: 'dev-user',
      timestamp: '2026-01-20T14:01:00Z',
      data: {
        tracker_id: 'NIU-199.2',
        event_type: 'ci_pass',
        delta: 0.1,
        score_after: 0.87,
      },
    },
    {
      id: 'evt-3',
      event: 'run.state_changed',
      ownerId: 'dev-user',
      timestamp: '2026-01-20T13:58:00Z',
      data: {
        tracker_id: 'NIU-183.4',
        status: 'review',
        action: 'review_requested',
      },
    },
    {
      id: 'evt-4',
      event: 'saga.completed',
      ownerId: 'dev-user',
      timestamp: '2026-01-20T13:52:00Z',
      data: {
        saga_id: '00000000-0000-0000-0000-000000000001',
        name: 'Auth Rewrite',
      },
    },
  ];

  return {
    async getState() {
      return { ...state };
    },

    async setRunning(running: boolean) {
      state = { ...state, running, updatedAt: new Date().toISOString() };
      log.push(`[mock] running = ${running}`);
    },

    async setThreshold(threshold: number) {
      state = { ...state, threshold, updatedAt: new Date().toISOString() };
    },

    async setAutoContinue(autoContinue: boolean) {
      state = { ...state, autoContinue, updatedAt: new Date().toISOString() };
    },

    async getLog() {
      return [...log];
    },

    async getActivityLog(limit = 100) {
      return activityLog.slice(0, limit);
    },
  };
}

/**
 * Create an in-memory ITingSessionService.
 */
export function createMockTingSessionService(): ITingSessionService {
  const sessions = new Map<string, SessionInfo>(SEED_SESSIONS.map((s) => [s.sessionId, { ...s }]));

  return {
    async getSessions() {
      return Array.from(sessions.values());
    },

    async getSession(id: string) {
      return sessions.get(id) ?? null;
    },

    async approve(sessionId: string) {
      const session = sessions.get(sessionId);
      if (session) {
        sessions.set(sessionId, { ...session, status: 'approved' });
      }
    },
  };
}

/**
 * Create an in-memory ITrackerBrowserService.
 */
export function createMockTrackerService(): ITrackerBrowserService {
  const projectMap = new Map<string, TrackerProject>(SEED_PROJECTS.map((p) => [p.id, p]));
  const milestoneMap = new Map<string, TrackerMilestone>(SEED_MILESTONES.map((m) => [m.id, m]));

  return {
    async listProjects() {
      return Array.from(projectMap.values());
    },

    async getProject(projectId: string) {
      const project = projectMap.get(projectId);
      if (!project) throw new Error(`Project not found: ${projectId}`);
      return project;
    },

    async listMilestones(projectId: string) {
      return Array.from(milestoneMap.values()).filter((m) => m.projectId === projectId);
    },

    async listIssues(projectId: string, milestoneId?: string) {
      const byProject = SEED_ISSUES.filter((i) =>
        SEED_MILESTONES.some((m) => m.id === i.milestoneId && m.projectId === projectId),
      );
      if (!milestoneId) return byProject;
      return byProject.filter((i) => i.milestoneId === milestoneId);
    },

    async importProject(
      projectId: string,
      repos: string[],
      _baseBranch?: string,
      instanceId?: string | null,
      options?: ImportProjectOptions,
    ) {
      const project = projectMap.get(projectId);
      const name = project?.name ?? projectId;
      const repoRefs =
        options?.repoRefs ?? repos.map((repo) => ({ repo, branch: _baseBranch ?? 'main' }));
      const target = options?.target;
      const saga: Saga = {
        id: `mock-import-${Date.now()}`,
        trackerId: projectId,
        trackerType: 'mock',
        slug: name.toLowerCase().replace(/\s+/g, '-'),
        name,
        repos: repoRefs.map((ref) => ref.repo),
        repoRefs,
        featureBranch: `feat/${name.toLowerCase().replace(/\s+/g, '-')}`,
        baseBranch: repoRefs[0]?.branch ?? _baseBranch ?? 'main',
        status: 'active',
        confidence: 50,
        createdAt: new Date().toISOString(),
        phaseSummary: { total: 0, completed: 0 },
        instanceId: target?.mode === 'instance' ? target.instanceId : (instanceId ?? undefined),
        targetTags: target?.mode === 'tags' ? target.tags : undefined,
        targetMatch: target?.mode === 'tags' ? (target.match ?? 'all') : undefined,
      };
      return saga;
    },
  };
}

/**
 * Create an in-memory IDispatchBus (Sleipnir stub).
 *
 * Immediately resolves all dispatches — callers apply optimistic updates
 * in the UI layer. Use in tests and local development.
 */
export function createMockDispatchBus(): IDispatchBus {
  return {
    async getQueue() {
      return [];
    },

    async getClusters() {
      return SEED_DISPATCH_CLUSTERS.map((cluster) => ({ ...cluster }));
    },

    async approve(items, options) {
      return items.map((item) => ({
        issueId: item.issueId,
        sessionId: `sess-${item.issueId}`,
        sessionName: item.issueId,
        status: 'spawned',
        clusterName: item.connectionId ?? options?.connectionId ?? 'local',
      }));
    },

    async dispatch(_runId: string): Promise<void> {
      // No-op in mock; UI handles optimistic status update.
    },

    async dispatchBatch(runIds: string[]): Promise<DispatchResult> {
      return { dispatched: runIds, failed: [] };
    },
  };
}

/**
 * Create an in-memory ITingSettingsService.
 */
export function createMockTingSettingsService(): ITingSettingsService {
  let flockConfig: FlockConfig = { ...SEED_FLOCK_CONFIG };
  let dispatchDefaults: DispatchDefaults = {
    ...SEED_DISPATCH_DEFAULTS,
    retryPolicy: { ...SEED_DISPATCH_DEFAULTS.retryPolicy },
  };
  let notificationSettings: NotificationSettings = { ...SEED_NOTIFICATION_SETTINGS };

  return {
    async getFlockConfig() {
      return { ...flockConfig };
    },

    async updateFlockConfig(patch) {
      flockConfig = { ...flockConfig, ...patch, updatedAt: new Date().toISOString() };
      return { ...flockConfig };
    },

    async getDispatchDefaults() {
      return { ...dispatchDefaults, retryPolicy: { ...dispatchDefaults.retryPolicy } };
    },

    async updateDispatchDefaults(patch) {
      const retryPolicy = patch.retryPolicy
        ? { ...dispatchDefaults.retryPolicy, ...patch.retryPolicy }
        : dispatchDefaults.retryPolicy;
      dispatchDefaults = {
        ...dispatchDefaults,
        ...patch,
        retryPolicy,
        updatedAt: new Date().toISOString(),
      };
      return { ...dispatchDefaults, retryPolicy: { ...dispatchDefaults.retryPolicy } };
    },

    async getNotificationSettings() {
      return { ...notificationSettings };
    },

    async updateNotificationSettings(patch) {
      notificationSettings = {
        ...notificationSettings,
        ...patch,
        updatedAt: new Date().toISOString(),
      };
      return { ...notificationSettings };
    },
  };
}

/**
 * Create an in-memory IAuditLogService.
 */
export function createMockAuditLogService(): IAuditLogService {
  const entries: AuditEntry[] = [...SEED_AUDIT_ENTRIES];

  return {
    async listAuditEntries(filter?: AuditFilter) {
      let result = [...entries];

      if (filter?.kinds && filter.kinds.length > 0) {
        result = result.filter((e) => filter.kinds!.includes(e.kind));
      }
      if (filter?.actor) {
        result = result.filter((e) => e.actor === filter.actor);
      }
      if (filter?.since) {
        result = result.filter((e) => e.createdAt >= filter.since!);
      }
      if (filter?.until) {
        result = result.filter((e) => e.createdAt <= filter.until!);
      }
      if (filter?.limit) {
        result = result.slice(0, filter.limit);
      }

      return result;
    },
  };
}

/**
 * Create an in-memory IWorkflowService backed by seed workflow DAGs.
 */
export function createMockWorkflowService(): IWorkflowService {
  const workflows = new Map<string, Workflow>(SEED_WORKFLOWS.map((w) => [w.id, w]));

  return {
    async listWorkflows() {
      return Array.from(workflows.values());
    },

    async getWorkflow(id: string) {
      return workflows.get(id) ?? null;
    },

    async saveWorkflow(workflow: Workflow) {
      workflows.set(workflow.id, workflow);
      return workflow;
    },

    async deleteWorkflow(id: string) {
      workflows.delete(id);
    },

    async launchWorkflow(workflowId: string, request: WorkflowLaunchRequest) {
      const workflow = workflows.get(workflowId);
      if (!workflow) {
        throw new Error(`Workflow ${workflowId} not found`);
      }
      const slugSource = request.prompt || workflow.name;
      const slug = (slugSource.toLowerCase().match(/[a-z0-9]+/g) ?? []).join('-').slice(0, 96);
      return {
        workflowId,
        workflowName: workflow.name,
        slug: slug || 'workflow',
        sessionId: `mock-session-${workflowId}`,
        sessionName: request.sessionName || `${workflow.name}-${slug || 'workflow'}`,
        status: 'starting',
        clusterName: 'mock',
      };
    },
  };
}

/**
 * Create an in-memory IResearchService backed by lightweight campaign records.
 */
export function createMockResearchService(): IResearchService {
  const now = new Date().toISOString();
  const workflows = new Map<string, Workflow>(
    SEED_WORKFLOWS.map((workflow) => [workflow.id, workflow]),
  );
  const seedCampaigns: ResearchCampaignDetail[] = [
    {
      id: '11111111-1111-4111-8111-111111111111',
      slug: 'rag-landscape',
      name: 'RAG landscape',
      ownerId: 'dev-user',
      workflowId: '00000000-0000-4000-8000-000000000001',
      workflowVersion: '1.0.0',
      workflowName: 'Research Campaign',
      sessionId: 'mock-session-rag',
      sessionName: 'RAG landscape',
      status: 'running',
      activeStageId: 'synthesize',
      stageState: [
        {
          stageId: 'frame',
          label: 'Frame the inquiry',
          status: 'complete',
          startedAt: now,
          completedAt: now,
        },
        {
          stageId: 'explore',
          label: 'Explore the evidence',
          status: 'complete',
          startedAt: now,
          completedAt: now,
        },
        {
          stageId: 'challenge',
          label: 'Challenge the thesis',
          status: 'complete',
          startedAt: now,
          completedAt: now,
        },
        {
          stageId: 'synthesize',
          label: 'Synthesize the research',
          status: 'active',
          startedAt: now,
          completedAt: null,
        },
      ],
      metadata: {
        question: 'What does the RAG tooling landscape look like?',
        mode: 'exploratory',
        audience: 'product leadership',
      },
      createdAt: now,
      updatedAt: now,
      lastActivityAt: now,
      completedAt: null,
      artifacts: [
        {
          path: 'research/campaigns/rag-landscape/brief.md',
          title: 'Brief',
          updatedAt: now,
          kind: 'brief',
          publishState: 'unknown',
          sourceIds: [],
          summary: 'Framing brief',
        },
        {
          path: 'research/campaigns/rag-landscape/final.md',
          title: 'Final',
          updatedAt: now,
          kind: 'final',
          publishState: 'published',
          sourceIds: ['src_123'],
          summary: 'Draft synthesis',
        },
      ],
      canonicalArtifacts: {
        brief: 'research/campaigns/rag-landscape/brief.md',
        final: 'research/campaigns/rag-landscape/final.md',
      },
    },
  ];
  const campaigns = new Map<string, ResearchCampaignDetail>(
    seedCampaigns.map((campaign) => [campaign.slug, campaign]),
  );

  return {
    async listCampaigns() {
      return Array.from(campaigns.values()).map((campaign) => ({ ...campaign }));
    },

    async getCampaign(slug: string) {
      return campaigns.get(slug) ?? null;
    },

    async createCampaign(request: CreateResearchCampaignRequest) {
      const workflow =
        (request.workflowId ? workflows.get(request.workflowId) : null) ??
        Array.from(workflows.values()).find((candidate: Workflow) =>
          candidate.tags?.includes('research'),
        ) ??
        Array.from(workflows.values())[0];
      const slug =
        (request.name || request.question)
          .toLowerCase()
          .match(/[a-z0-9]+/g)
          ?.join('-')
          .slice(0, 96) || `campaign-${campaigns.size + 1}`;
      const createdAt = new Date().toISOString();
      const campaign: ResearchCampaignDetail = {
        id: crypto.randomUUID(),
        slug,
        name: request.name || request.question.slice(0, 80),
        ownerId: 'dev-user',
        workflowId: workflow?.id ?? '00000000-0000-4000-8000-000000000001',
        workflowVersion: workflow?.version ?? '1.0.0',
        workflowName: workflow?.name ?? 'Research Campaign',
        sessionId: `mock-session-${slug}`,
        sessionName: request.name || slug,
        status: 'running',
        activeStageId: 'frame',
        stageState: [
          {
            stageId: 'frame',
            label: 'Frame the inquiry',
            status: 'active',
            startedAt: createdAt,
            completedAt: null,
          },
        ],
        metadata: {
          question: request.question,
          mode: request.mode ?? 'exploratory',
          audience: request.audience ?? '',
          deliverable: request.deliverable ?? '',
          success: request.success ?? '',
          constraints: request.constraints ?? [],
          repo: request.repo ?? '',
          branch: request.branch ?? '',
          connection_id: request.connectionId ?? '',
          cluster_name:
            SEED_DISPATCH_CLUSTERS.find((cluster) => cluster.connectionId === request.connectionId)
              ?.name ?? 'Mac mini',
        },
        createdAt,
        updatedAt: createdAt,
        lastActivityAt: createdAt,
        completedAt: null,
        artifacts: [],
        canonicalArtifacts: {},
      };
      campaigns.set(slug, campaign);
      return campaign;
    },

    async updateCampaign(slug: string, request: UpdateResearchCampaignRequest) {
      const current = campaigns.get(slug);
      if (!current) throw new Error(`Campaign ${slug} not found`);
      const updated: ResearchCampaignDetail = {
        ...current,
        name: request.name ?? current.name,
        status: (request.status as ResearchCampaign['status']) ?? current.status,
        metadata: { ...current.metadata, ...(request.metadata ?? {}) },
        updatedAt: new Date().toISOString(),
      };
      campaigns.set(slug, updated);
      return updated;
    },

    async deleteCampaign(slug: string) {
      campaigns.delete(slug);
    },

    async getArtifact(slug: string, path: string) {
      const artifact = campaigns.get(slug)?.artifacts.find((item) => item.path === path);
      if (!artifact) return null;
      return {
        ...artifact,
        content: `# ${artifact.title}\n\nMock content for ${artifact.path}.`,
      } satisfies CampaignArtifactDetail;
    },
  };
}

export function createMockSpecsService(): ISpecsService {
  const now = new Date().toISOString();
  const seedCampaign: ResearchCampaignDetail = {
    id: '22222222-2222-4222-8222-222222222222',
    slug: 'smart-device-control-protocol',
    name: 'SDCP operator specification',
    ownerId: 'dev-user',
    workflowId: '96ecf5df-18a0-542b-9df6-aef6aef6a5db',
    workflowVersion: '1.0.0',
    workflowName: 'Specification Stack',
    sessionId: 'mock-session-spec',
    sessionName: 'SDCP operator specification',
    status: 'blocked',
    activeStageId: 'spec-prd-review',
    stageState: [
      {
        stageId: 'spec-frame',
        label: 'Frame initiative',
        status: 'complete',
        startedAt: now,
        completedAt: now,
      },
      {
        stageId: 'spec-prd',
        label: 'Draft PRD',
        status: 'complete',
        startedAt: now,
        completedAt: now,
      },
      {
        stageId: 'spec-prd-review',
        label: 'Review PRD',
        status: 'blocked',
        startedAt: now,
        completedAt: null,
        reason: 'Review required',
      },
    ],
    metadata: {
      surface: 'ting.specs',
      prompt: 'Plan SDCP v3.0.0 as a Kubernetes operator for 3D printers.',
      repos: ['niuulabs/volundr'],
      pending_workflow_gates: [
        {
          id: 'mock-prd-gate',
          node_id: 'spec-prd-gate',
          status: 'pending',
          summary: 'PRD approval gate',
          instructions:
            'Approve the PRD or request changes with feedback before SRD drafting begins.',
        },
      ],
    },
    createdAt: now,
    updatedAt: now,
    lastActivityAt: now,
    completedAt: null,
    artifacts: [
      {
        path: 'specifications/smart-device-control-protocol/00-brief.md',
        title: 'Brief',
        updatedAt: now,
        kind: 'brief',
        publishState: 'unknown',
        sourceIds: [],
        summary: 'Initiative framing',
      },
      {
        path: 'specifications/smart-device-control-protocol/10-prd.md',
        title: 'PRD',
        updatedAt: now,
        kind: 'prd',
        publishState: 'unknown',
        sourceIds: [],
        summary: 'Product requirements draft',
      },
      {
        path: 'specifications/smart-device-control-protocol/11-prd-review.md',
        title: 'PRD Review',
        updatedAt: now,
        kind: 'prd_review',
        publishState: 'unknown',
        sourceIds: [],
        summary: 'Review notes',
      },
    ],
    canonicalArtifacts: {
      brief: 'specifications/smart-device-control-protocol/00-brief.md',
      prd: 'specifications/smart-device-control-protocol/10-prd.md',
      prd_review: 'specifications/smart-device-control-protocol/11-prd-review.md',
    },
  };
  const campaigns = new Map<string, ResearchCampaignDetail>([[seedCampaign.slug, seedCampaign]]);

  return {
    async listCampaigns() {
      return Array.from(campaigns.values()).map((campaign) => ({ ...campaign }));
    },

    async getCampaign(slug: string) {
      return campaigns.get(slug) ?? null;
    },

    async createCampaign(request: CreateSpecCampaignRequest) {
      const slug =
        (request.name || request.prompt)
          .toLowerCase()
          .match(/[a-z0-9]+/g)
          ?.join('-')
          .slice(0, 96) || `spec-${campaigns.size + 1}`;
      const createdAt = new Date().toISOString();
      const campaign: ResearchCampaignDetail = {
        id: crypto.randomUUID(),
        slug,
        name: request.name || request.prompt.slice(0, 80),
        ownerId: 'dev-user',
        workflowId: request.workflowId ?? '96ecf5df-18a0-542b-9df6-aef6aef6a5db',
        workflowVersion: '1.0.0',
        workflowName: 'Specification Stack',
        sessionId: `mock-session-${slug}`,
        sessionName: request.name || slug,
        status: 'running',
        activeStageId: 'spec-frame',
        stageState: [
          {
            stageId: 'spec-frame',
            label: 'Frame initiative',
            status: 'active',
            startedAt: createdAt,
            completedAt: null,
          },
        ],
        metadata: {
          surface: 'ting.specs',
          prompt: request.prompt,
          context: request.context ?? '',
          repo: request.repo ?? request.repos?.[0] ?? '',
          repos: request.repos ?? (request.repo ? [request.repo] : []),
          branch: request.branch ?? '',
          connection_id: request.connectionId ?? '',
        },
        createdAt,
        updatedAt: createdAt,
        lastActivityAt: createdAt,
        completedAt: null,
        artifacts: [],
        canonicalArtifacts: {},
      };
      campaigns.set(slug, campaign);
      return campaign;
    },

    async deleteCampaign(slug: string) {
      campaigns.delete(slug);
    },

    async getArtifact(slug: string, path: string) {
      const artifact = campaigns.get(slug)?.artifacts.find((item) => item.path === path);
      if (!artifact) return null;
      return {
        ...artifact,
        content: `# ${artifact.title}\n\nMock specification content for ${artifact.path}.`,
      } satisfies CampaignArtifactDetail;
    },

    async reviewCampaign(slug: string, request: ReviewSpecCampaignRequest) {
      const current = campaigns.get(slug);
      if (!current) throw new Error(`Spec campaign ${slug} not found`);
      const updated: ResearchCampaignDetail = {
        ...current,
        status: request.decision === 'approve' ? 'running' : 'blocked',
        metadata: {
          ...current.metadata,
          pending_workflow_gates:
            request.decision === 'approve' ? [] : current.metadata.pending_workflow_gates,
          latest_spec_review: {
            decision: request.decision,
            notes: request.notes ?? '',
            gate_id: request.gateId ?? '',
            node_id: request.nodeId ?? '',
          },
        },
        updatedAt: new Date().toISOString(),
      };
      campaigns.set(slug, updated);
      return updated;
    },
  };
}
