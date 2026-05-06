/**
 * Mock adapters for Völundr ports — used in tests and dev mode.
 */
import type { IVolundrService } from '../ports/IVolundrService';
import type { IClusterAdapter } from '../ports/IClusterAdapter';
import type { ISessionStore } from '../ports/ISessionStore';
import type { ITemplateStore } from '../ports/ITemplateStore';
import type { IPtyStream } from '../ports/IPtyStream';
import type { IMetricsStream } from '../ports/IMetricsStream';
import type { IFileSystemPort, FileTreeNode } from '../ports/IFileSystemPort';
import type {
  VolundrSession,
  VolundrStats,
  VolundrMessage,
  StoredCredential,
  FeatureModule,
  SecretType,
  SessionDefinition,
  IntegrationConnection,
  ClusterResourceInfo,
  McpServerConfig,
  VolundrModel,
  VolundrPreset,
  VolundrRepo,
  TrackerIssue,
  VolundrWorkspace,
} from '../models/volundr.model';
import type { Cluster } from '../domain/cluster';
import type { Session } from '../domain/session';
import type { Template } from '../domain/template';

// ---------------------------------------------------------------------------
// Seed data
// ---------------------------------------------------------------------------

const SEED_SESSIONS: VolundrSession[] = [
  {
    id: 'sess-1',
    name: 'feat/refactor-auth',
    source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'feat/refactor-auth' },
    status: 'running',
    model: 'claude-sonnet',
    lastActive: Date.now() - 60_000,
    messageCount: 14,
    tokensUsed: 8_400,
    taskType: 'skuld-claude',
    activityState: 'active',
  },
  {
    id: 'sess-2',
    name: 'fix/login-redirect',
    source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'fix/login-redirect' },
    status: 'stopped',
    model: 'claude-haiku',
    lastActive: Date.now() - 3_600_000,
    messageCount: 6,
    tokensUsed: 1_200,
    taskType: 'skuld-claude',
    activityState: null,
  },
];

const SEED_STATS: VolundrStats = {
  activeSessions: 8,
  totalSessions: 12,
  tokensToday: 1_080_000,
  localTokens: 0,
  cloudTokens: 1_080_000,
  costToday: 33.72,
  sparklines: {
    activePods: [3, 4, 3, 5, 6, 5, 7, 8, 7, 6, 5, 6, 7, 8, 7, 7, 6, 7, 8, 7, 6, 7, 7, 8],
    tokensToday: [
      20_000, 30_000, 25_000, 40_000, 35_000, 45_000, 60_000, 55_000, 50_000, 40_000, 45_000,
      55_000, 60_000, 65_000, 55_000, 50_000, 45_000, 60_000, 70_000, 65_000, 55_000, 60_000,
      65_000, 75_000,
    ],
    costToday: [1, 2, 2, 3, 3, 4, 5, 5, 5, 4, 4, 5, 6, 7, 6, 6, 5, 6, 7, 7, 6, 7, 7, 8],
    gpus: [4, 5, 5, 6, 7, 7, 8, 9, 8, 7, 6, 7, 8, 9, 8, 8, 7, 8, 9, 9, 8, 9, 9, 9],
  },
};

const SEED_CREDENTIALS: StoredCredential[] = [
  {
    id: 'cred-1',
    name: 'anthropic-key',
    secretType: 'api_key',
    keys: ['ANTHROPIC_API_KEY'],
    scope: 'global',
    used: 42,
    metadata: {},
    createdAt: '2026-01-03T12:00:00Z',
    updatedAt: '2d ago',
  },
  {
    id: 'cred-2',
    name: 'openai-key',
    secretType: 'api_key',
    keys: ['OPENAI_API_KEY'],
    scope: 'global',
    used: 17,
    metadata: {},
    createdAt: '2026-01-03T12:00:00Z',
    updatedAt: '2d ago',
  },
  {
    id: 'cred-3',
    name: 'google-key',
    secretType: 'api_key',
    keys: ['GOOGLE_API_KEY'],
    scope: 'global',
    used: 3,
    metadata: {},
    createdAt: '2026-01-03T12:00:00Z',
    updatedAt: '2d ago',
  },
  {
    id: 'cred-4',
    name: 'aws-mimir',
    secretType: 'api_key',
    keys: ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'],
    scope: 'template:mimir-embeddings',
    used: 6,
    metadata: {},
    createdAt: '2026-01-02T12:00:00Z',
    updatedAt: '3d ago',
  },
  {
    id: 'cred-5',
    name: 'hf-hub',
    secretType: 'api_key',
    keys: ['HF_TOKEN'],
    scope: 'global',
    used: 4,
    metadata: {},
    createdAt: '2026-01-01T12:00:00Z',
    updatedAt: '2w ago',
  },
  {
    id: 'cred-6',
    name: 'github-niuu',
    secretType: 'git_credential',
    keys: ['GIT_USERNAME', 'GIT_PASSWORD'],
    scope: 'global',
    used: 128,
    metadata: {},
    createdAt: '2025-12-18T12:00:00Z',
    updatedAt: '1w ago',
  },
  {
    id: 'cred-7',
    name: 'linear-oauth',
    secretType: 'oauth_token',
    keys: ['LINEAR_TOKEN', 'LINEAR_REFRESH'],
    scope: 'global',
    used: 14,
    metadata: {},
    createdAt: '2025-12-27T12:00:00Z',
    updatedAt: '6h ago',
  },
  {
    id: 'cred-8',
    name: 'ssh-deploy',
    secretType: 'ssh_key',
    keys: ['id_ed25519', 'id_ed25519.pub'],
    scope: 'template:niuu-platform',
    used: 2,
    metadata: {},
    createdAt: '2025-11-15T12:00:00Z',
    updatedAt: '1mo ago',
  },
  {
    id: 'cred-9',
    name: 'tls-niuu-internal',
    secretType: 'tls_cert',
    keys: ['tls.crt', 'tls.key', 'ca.crt'],
    scope: 'cluster:valaskjalf',
    used: 0,
    metadata: {},
    createdAt: '2025-10-18T12:00:00Z',
    updatedAt: '3mo ago',
  },
];

const SEED_MODELS: Record<string, VolundrModel> = {
  'sonnet-primary': {
    name: 'Claude Sonnet',
    provider: 'cloud',
    tier: 'balanced',
    color: '#e7d2b4',
    cost: '$$$',
  },
  'gpt-5-codex': {
    name: 'GPT-5 Codex',
    provider: 'cloud',
    tier: 'execution',
    color: '#b9d8ff',
    cost: '$$$',
  },
};

const SEED_REPOS: VolundrRepo[] = [
  {
    provider: 'github',
    org: 'niuulabs',
    name: 'volundr',
    cloneUrl: 'github.com/niuulabs/volundr',
    url: 'https://github.com/niuulabs/volundr',
    defaultBranch: 'main',
    branches: ['main', 'develop', 'feat/host-profiles'],
  },
  {
    provider: 'github',
    org: 'niuulabs',
    name: 'bifrost',
    cloneUrl: 'github.com/niuulabs/bifrost',
    url: 'https://github.com/niuulabs/bifrost',
    defaultBranch: 'main',
    branches: ['main', 'feat/provider-router'],
  },
];

const SEED_WORKSPACES: VolundrWorkspace[] = [
  {
    id: 'ws-1',
    pvcName: 'pvc-volundr-main',
    sessionId: 'sess-arch-1',
    ownerId: 'dev-user',
    tenantId: 't1',
    sizeGb: 14,
    status: 'archived',
    createdAt: '2026-04-01T12:00:00Z',
    archivedAt: '2026-04-20T12:00:00Z',
    sessionName: 'main',
    sourceUrl: 'github.com/niuulabs/volundr',
    sourceRef: 'main',
  },
  {
    id: 'ws-2',
    pvcName: 'pvc-bifrost-provider-router',
    sessionId: 'sess-arch-2',
    ownerId: 'dev-user',
    tenantId: 't1',
    sizeGb: 8,
    status: 'archived',
    createdAt: '2026-04-02T12:00:00Z',
    archivedAt: '2026-04-18T12:00:00Z',
    sessionName: 'provider-router',
    sourceUrl: 'github.com/niuulabs/bifrost',
    sourceRef: 'feat/provider-router',
  },
];

const SEED_INTEGRATIONS: IntegrationConnection[] = [
  {
    id: 'github-primary',
    createdAt: '2026-04-01T12:00:00Z',
    updatedAt: '2026-04-24T12:00:00Z',
  },
  {
    id: 'linear-main',
    createdAt: '2026-04-02T12:00:00Z',
    updatedAt: '2026-04-23T12:00:00Z',
  },
];

const SEED_TRACKER_ISSUES: TrackerIssue[] = [
  {
    id: 'issue-1',
    identifier: 'NIU-801',
    title: 'Polish forge launch defaults',
    status: 'in_progress',
    url: 'https://linear.app/niuu/issue/NIU-801',
  },
  {
    id: 'issue-2',
    identifier: 'NIU-802',
    title: 'Hook tracker issue launch context into sessions',
    status: 'todo',
    url: 'https://linear.app/niuu/issue/NIU-802',
  },
];

const SEED_CLUSTER_RESOURCES: ClusterResourceInfo = {
  resourceTypes: [
    { name: 'cpu', resourceKey: 'cpu', displayName: 'CPU', unit: 'cores', category: 'compute' },
    {
      name: 'memory',
      resourceKey: 'memory',
      displayName: 'Memory',
      unit: 'bytes',
      category: 'memory',
    },
    { name: 'gpu', resourceKey: 'gpu', displayName: 'GPU', unit: 'count', category: 'accelerator' },
  ],
  nodes: [
    {
      name: 'forge-a',
      labels: {},
      allocatable: { cpu: '16', memory: '32Gi', gpu: '1' },
      allocated: { cpu: '4', memory: '8Gi', gpu: '0' },
      available: { cpu: '12', memory: '24Gi', gpu: '1' },
    },
    {
      name: 'forge-b',
      labels: {},
      allocatable: { cpu: '24', memory: '48Gi', gpu: '2' },
      allocated: { cpu: '8', memory: '16Gi', gpu: '1' },
      available: { cpu: '16', memory: '32Gi', gpu: '1' },
    },
  ],
};

const SEED_MCP_SERVERS: McpServerConfig[] = [
  {
    name: 'filesystem',
    type: 'stdio',
    command: 'uvx',
    args: ['mcp-filesystem', '/workspace'],
  },
  {
    name: 'git',
    type: 'stdio',
    command: 'uvx',
    args: ['mcp-git'],
  },
];

const SEED_PRESETS: VolundrPreset[] = [
  {
    id: 'preset-fast-review',
    name: 'fast-review',
    description: 'Quick review setup for the main repo',
    isDefault: true,
    createdAt: '2026-04-01T12:00:00Z',
    updatedAt: '2026-04-20T12:00:00Z',
    cliTool: 'claude',
    workloadType: 'skuld-claude',
    model: 'sonnet-primary',
    systemPrompt: 'Review changes and summarize the main risks.',
    resourceConfig: { cpu: '2', memory: '8Gi' },
    mcpServers: [SEED_MCP_SERVERS[0]!],
    terminalSidecar: { enabled: true, allowedCommands: [] },
    skills: [],
    rules: [],
    envVars: { LOG_LEVEL: 'info' },
    envSecretRefs: ['github-niuu'],
    source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
    integrationIds: ['github-primary'],
    setupScripts: ['pnpm install'],
    workloadConfig: {},
  },
];

const SEED_CLUSTERS: Cluster[] = [
  // ── Original clusters (other tests depend on exact shape) ─────────────
  {
    id: 'cl-eitri',
    realm: 'asgard',
    name: 'Eitri',
    kind: 'primary',
    status: 'healthy',
    region: 'ca-hamilton-1',
    capacity: { cpu: 64, memMi: 131_072, gpu: 4 },
    used: { cpu: 12, memMi: 24_576, gpu: 2 },
    disk: { usedGi: 820, totalGi: 2048, systemGi: 120, podsGi: 580, logsGi: 120 },
    nodes: [
      { id: 'n-1', status: 'ready', role: 'worker' },
      { id: 'n-2', status: 'ready', role: 'worker' },
    ],
    pods: [
      {
        name: 'volundr-auth-refactor-7b2f',
        status: 'running',
        startedAt: new Date(Date.now() - 3_600_000).toISOString(),
        cpuUsed: 2.1,
        cpuLimit: 4,
        memUsedMi: 5_400,
        memLimitMi: 16_384,
        restarts: 0,
      },
    ],
    runningSessions: 1,
    queuedProvisions: 0,
  },
  {
    id: 'cl-brokkr',
    realm: 'midgard',
    name: 'Brokkr',
    kind: 'edge',
    status: 'warning',
    region: 'ca-toronto',
    capacity: { cpu: 32, memMi: 65_536, gpu: 0 },
    used: { cpu: 8, memMi: 16_384, gpu: 0 },
    disk: { usedGi: 310, totalGi: 1024, systemGi: 80, podsGi: 180, logsGi: 50 },
    nodes: [
      { id: 'n-3', status: 'ready', role: 'worker' },
      { id: 'n-4', status: 'notready', role: 'worker' },
      { id: 'n-5', status: 'cordoned', role: 'worker' },
    ],
    pods: [
      {
        name: 'mimir-bge-reindex-a1c3',
        status: 'running',
        startedAt: new Date(Date.now() - 2_400_000).toISOString(),
        cpuUsed: 3.6,
        cpuLimit: 4,
        memUsedMi: 22_100,
        memLimitMi: 32_768,
        restarts: 1,
      },
      {
        name: 'ravn-triggers-ui-e4d9',
        status: 'idle',
        startedAt: new Date(Date.now() - 28_800_000).toISOString(),
        cpuUsed: 0.2,
        cpuLimit: 2,
        memUsedMi: 1_100,
        memLimitMi: 4_096,
        restarts: 0,
      },
    ],
    runningSessions: 2,
    queuedProvisions: 1,
  },
  // ── Additional clusters for forge-overview visual density ─────────────
  {
    id: 'cl-valhalla',
    realm: 'asgard',
    name: 'Valhalla',
    kind: 'gpu',
    status: 'healthy',
    region: 'ca-hamilton-2',
    capacity: { cpu: 64, memMi: 131_072, gpu: 8 },
    used: { cpu: 8, memMi: 32_768, gpu: 6 },
    disk: { usedGi: 400, totalGi: 2048, systemGi: 100, podsGi: 250, logsGi: 50 },
    nodes: [{ id: 'n-6', status: 'ready', role: 'worker' }],
    pods: [],
    runningSessions: 1,
    queuedProvisions: 0,
  },
  {
    id: 'cl-noatun',
    realm: 'midgard',
    name: 'Nóatún',
    kind: 'local',
    status: 'healthy',
    region: 'eu-amsterdam',
    capacity: { cpu: 32, memMi: 65_536, gpu: 0 },
    used: { cpu: 4, memMi: 12_288, gpu: 0 },
    disk: { usedGi: 200, totalGi: 1024, systemGi: 60, podsGi: 110, logsGi: 30 },
    nodes: [{ id: 'n-7', status: 'ready', role: 'worker' }],
    pods: [],
    runningSessions: 0,
    queuedProvisions: 0,
  },
  {
    id: 'cl-glitnir',
    realm: 'midgard',
    name: 'Glitnir',
    kind: 'observ',
    status: 'healthy',
    region: 'us-east',
    capacity: { cpu: 32, memMi: 65_536, gpu: 0 },
    used: { cpu: 2, memMi: 8_192, gpu: 0 },
    disk: { usedGi: 180, totalGi: 1024, systemGi: 40, podsGi: 100, logsGi: 40 },
    nodes: [{ id: 'n-8', status: 'ready', role: 'worker' }],
    pods: [],
    runningSessions: 0,
    queuedProvisions: 0,
  },
  {
    id: 'cl-jarnvidr',
    realm: 'jotunheim',
    name: 'Járnviðr',
    kind: 'media',
    status: 'healthy',
    region: 'ap-tokyo',
    capacity: { cpu: 16, memMi: 32_768, gpu: 3 },
    used: { cpu: 1, memMi: 4_096, gpu: 1 },
    disk: { usedGi: 90, totalGi: 512, systemGi: 30, podsGi: 40, logsGi: 20 },
    nodes: [{ id: 'n-9', status: 'ready', role: 'worker' }],
    pods: [],
    runningSessions: 0,
    queuedProvisions: 0,
  },
];

const SEED_DOMAIN_SESSIONS: Session[] = [
  // ── Booting ──────────────────────────────────────────────
  {
    id: 'niuu-integration-tests',
    ravnId: 'r-integ',
    personaName: 's-4915',
    templateId: 'tpl-default',
    clusterId: 'Valaskjálf',
    state: 'provisioning',
    startedAt: new Date(Date.now() - 60_000).toISOString(),
    connectionType: 'cli',
    bootProgress: 0.3,
    preview: 'cloning',
    resources: {
      cpuRequest: 2,
      cpuLimit: 4,
      cpuUsed: 0,
      memRequestMi: 1_024,
      memLimitMi: 2_048,
      memUsedMi: 0,
      gpuCount: 0,
    },
    env: {},
    events: [
      {
        ts: new Date(Date.now() - 60_000).toISOString(),
        kind: 'provisioning',
        body: 'cloning repo',
      },
    ],
  },
  // ── Running / idle ───────────────────────────────────────
  {
    id: 'laptop-volundr-local',
    ravnId: 'r-local',
    personaName: '~/code/niuu',
    templateId: 'tpl-default',
    clusterId: 'Eitri',
    state: 'running',
    startedAt: new Date(Date.now() - 14_400_000).toISOString(),
    readyAt: new Date(Date.now() - 14_390_000).toISOString(),
    lastActivityAt: new Date(Date.now() - 30_000).toISOString(),
    connectionType: 'cli',
    tokensIn: 10_000,
    tokensOut: 3_600,
    costCents: 44,
    preview: 'reading volundr/web/src/modules/volundr/pages/V…',
    resources: {
      cpuRequest: 2,
      cpuLimit: 4,
      cpuUsed: 1.2,
      memRequestMi: 2_048,
      memLimitMi: 4_096,
      memUsedMi: 1_800,
      gpuCount: 0,
    },
    env: {},
    events: [],
  },
  {
    id: 'mimir-bge-reindex',
    ravnId: 'r-bge',
    personaName: 'mimir@reindex-bge',
    templateId: 'tpl-gpu',
    clusterId: 'Valhalla',
    state: 'running',
    startedAt: new Date(Date.now() - 7_200_000).toISOString(),
    readyAt: new Date(Date.now() - 7_190_000).toISOString(),
    lastActivityAt: new Date(Date.now() - 120_000).toISOString(),
    connectionType: 'api',
    tokensIn: 18_000,
    tokensOut: 7_500,
    costCents: 88,
    preview: 'running bge-large-en-v1.5 over 48k docs…',
    resources: {
      cpuRequest: 4,
      cpuLimit: 8,
      cpuUsed: 3.2,
      memRequestMi: 8_192,
      memLimitMi: 16_384,
      memUsedMi: 12_200,
      gpuCount: 1,
    },
    env: {},
    events: [],
  },
  {
    id: 'aider-css-migration',
    ravnId: 'r-css',
    personaName: 'volundr@css-token-migrate',
    templateId: 'tpl-default',
    clusterId: 'Valaskjálf',
    state: 'running',
    startedAt: new Date(Date.now() - 3_600_000).toISOString(),
    readyAt: new Date(Date.now() - 3_590_000).toISOString(),
    lastActivityAt: new Date(Date.now() - 90_000).toISOString(),
    connectionType: 'cli',
    tokensIn: 16_000,
    tokensOut: 8_000,
    costCents: 32,
    preview: 'replacing hardcoded hex with token vars · 44 fi…',
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0.8,
      memRequestMi: 512,
      memLimitMi: 1_024,
      memUsedMi: 480,
      gpuCount: 0,
    },
    env: {},
    events: [],
  },
  {
    id: 'ravn-triggers-ui',
    ravnId: 'r-trig',
    personaName: 'volundr@ravn-ui',
    templateId: 'tpl-default',
    clusterId: 'Valaskjálf',
    state: 'running',
    startedAt: new Date(Date.now() - 10_800_000).toISOString(),
    readyAt: new Date(Date.now() - 10_790_000).toISOString(),
    lastActivityAt: new Date(Date.now() - 200_000).toISOString(),
    connectionType: 'ide',
    tokensIn: 180_000,
    tokensOut: 71_300,
    costCents: 842,
    preview: 'assistant: wiring fan-in chips into EventSubsc…',
    resources: {
      cpuRequest: 2,
      cpuLimit: 4,
      cpuUsed: 1.5,
      memRequestMi: 2_048,
      memLimitMi: 4_096,
      memUsedMi: 2_800,
      gpuCount: 0,
    },
    env: {},
    events: [],
  },
  {
    id: 'observatory-canvas-perf',
    ravnId: 'r-perf',
    personaName: 'volundr@obs-perf',
    templateId: 'tpl-default',
    clusterId: 'Valaskjálf',
    state: 'running',
    startedAt: new Date(Date.now() - 5_400_000).toISOString(),
    readyAt: new Date(Date.now() - 5_390_000).toISOString(),
    lastActivityAt: new Date(Date.now() - 300_000).toISOString(),
    connectionType: 'cli',
    tokensIn: 70_000,
    tokensOut: 26_600,
    costCents: 328,
    preview: 'running jest --watch on modified files · 12 pas…',
    resources: {
      cpuRequest: 2,
      cpuLimit: 4,
      cpuUsed: 1.0,
      memRequestMi: 2_048,
      memLimitMi: 4_096,
      memUsedMi: 1_600,
      gpuCount: 0,
    },
    env: {},
    events: [],
  },
  {
    id: 'ds-7',
    ravnId: 'r-7',
    personaName: 'skald@auth-flow',
    templateId: 'tpl-default',
    clusterId: 'Valaskjálf',
    state: 'running',
    startedAt: new Date(Date.now() - 2_400_000).toISOString(),
    readyAt: new Date(Date.now() - 2_390_000).toISOString(),
    lastActivityAt: new Date(Date.now() - 180_000).toISOString(),
    connectionType: 'cli',
    tokensIn: 8_000,
    tokensOut: 3_200,
    costCents: 18,
    preview: 'refactoring auth middleware for OIDC adapter',
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0.6,
      memRequestMi: 512,
      memLimitMi: 1_024,
      memUsedMi: 400,
      gpuCount: 0,
    },
    env: {},
    events: [],
  },
  {
    id: 'ds-8',
    ravnId: 'r-8',
    personaName: 'herald@api-docs',
    templateId: 'tpl-default',
    clusterId: 'Valaskjálf',
    state: 'idle',
    startedAt: new Date(Date.now() - 18_000_000).toISOString(),
    readyAt: new Date(Date.now() - 17_990_000).toISOString(),
    lastActivityAt: new Date(Date.now() - 600_000).toISOString(),
    connectionType: 'ide',
    tokensIn: 2_400,
    tokensOut: 800,
    costCents: 5,
    preview: 'writing unit tests for session lifecycle',
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0.05,
      memRequestMi: 512,
      memLimitMi: 1_024,
      memUsedMi: 260,
      gpuCount: 0,
    },
    env: {},
    events: [],
  },
  {
    id: 'ds-9',
    ravnId: 'r-9',
    personaName: 'bard@pipeline',
    templateId: 'tpl-default',
    clusterId: 'Valaskjálf',
    state: 'running',
    startedAt: new Date(Date.now() - 1_200_000).toISOString(),
    readyAt: new Date(Date.now() - 1_190_000).toISOString(),
    lastActivityAt: new Date(Date.now() - 45_000).toISOString(),
    connectionType: 'cli',
    tokensIn: 5_000,
    tokensOut: 2_000,
    costCents: 12,
    preview: 'implementing batch import pipeline',
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0.9,
      memRequestMi: 512,
      memLimitMi: 1_024,
      memUsedMi: 420,
      gpuCount: 0,
    },
    env: {},
    events: [],
  },
  // ── Original sessions (other tests depend on these) ─────
  {
    id: 'ds-1',
    ravnId: 'r1',
    personaName: 'skald',
    templateId: 'tpl-default',
    clusterId: 'cl-eitri',
    state: 'running',
    startedAt: new Date(Date.now() - 3_600_000).toISOString(),
    readyAt: new Date(Date.now() - 3_590_000).toISOString(),
    lastActivityAt: new Date(Date.now() - 60_000).toISOString(),
    connectionType: 'cli',
    tokensIn: 4_200,
    tokensOut: 1_800,
    costCents: 8,
    preview: 'Refactoring the auth middleware to use the new OIDC adapter pattern',
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0.4,
      memRequestMi: 512,
      memLimitMi: 1_024,
      memUsedMi: 320,
      gpuCount: 0,
      diskUsedMi: 2_048,
      diskLimitMi: 10_240,
    },
    files: { added: 3, modified: 7, deleted: 1 },
    env: {},
    events: [
      {
        ts: new Date(Date.now() - 3_600_000).toISOString(),
        kind: 'requested',
        body: 'session requested',
      },
      {
        ts: new Date(Date.now() - 3_595_000).toISOString(),
        kind: 'provisioning',
        body: 'pod scheduling',
      },
      { ts: new Date(Date.now() - 3_590_000).toISOString(), kind: 'ready', body: 'pod ready' },
      {
        ts: new Date(Date.now() - 3_580_000).toISOString(),
        kind: 'running',
        body: 'session active',
      },
    ],
  },
  {
    id: 'ds-2',
    ravnId: 'r2',
    personaName: 'herald',
    templateId: 'tpl-default',
    clusterId: 'cl-eitri',
    state: 'idle',
    startedAt: new Date(Date.now() - 7_200_000).toISOString(),
    readyAt: new Date(Date.now() - 7_190_000).toISOString(),
    lastActivityAt: new Date(Date.now() - 1_800_000).toISOString(),
    connectionType: 'ide',
    tokensIn: 600,
    tokensOut: 200,
    costCents: 2,
    preview: 'Writing unit tests for the session lifecycle state machine',
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0.05,
      memRequestMi: 512,
      memLimitMi: 1_024,
      memUsedMi: 280,
      gpuCount: 0,
    },
    env: { NODE_ENV: 'development' },
    events: [
      {
        ts: new Date(Date.now() - 7_200_000).toISOString(),
        kind: 'requested',
        body: 'session requested',
      },
      { ts: new Date(Date.now() - 7_190_000).toISOString(), kind: 'ready', body: 'pod ready' },
      {
        ts: new Date(Date.now() - 1_800_000).toISOString(),
        kind: 'idle',
        body: 'no activity detected',
      },
    ],
  },
  {
    id: 'ds-3',
    ravnId: 'r3',
    personaName: 'bard',
    templateId: 'tpl-default',
    clusterId: 'cl-eitri',
    state: 'provisioning',
    startedAt: new Date(Date.now() - 120_000).toISOString(),
    connectionType: 'api',
    bootProgress: 0.45,
    resources: {
      cpuRequest: 2,
      cpuLimit: 4,
      cpuUsed: 0,
      memRequestMi: 1_024,
      memLimitMi: 2_048,
      memUsedMi: 0,
      gpuCount: 1,
    },
    env: {},
    events: [
      {
        ts: new Date(Date.now() - 120_000).toISOString(),
        kind: 'requested',
        body: 'session requested',
      },
      {
        ts: new Date(Date.now() - 90_000).toISOString(),
        kind: 'provisioning',
        body: 'pod scheduling',
      },
    ],
  },
  {
    id: 'ds-4',
    ravnId: 'r4',
    personaName: 's-4855',
    templateId: 'tpl-default',
    clusterId: 'cl-eitri',
    state: 'failed',
    startedAt: new Date(Date.now() - 86_400_000).toISOString(),
    terminatedAt: new Date(Date.now() - 86_000_000).toISOString(),
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0,
      memRequestMi: 512,
      memLimitMi: 1_024,
      memUsedMi: 0,
      gpuCount: 0,
    },
    env: {},
    events: [
      {
        ts: new Date(Date.now() - 86_400_000).toISOString(),
        kind: 'requested',
        body: 'session requested',
      },
      {
        ts: new Date(Date.now() - 86_000_000).toISOString(),
        kind: 'failed',
        body: 'CredentialNotFound: google-key scope=global — key rotation in progress',
      },
    ],
  },
  {
    id: 'ds-5',
    ravnId: 'r5',
    personaName: 'scout',
    templateId: 'tpl-default',
    clusterId: 'cl-eitri',
    state: 'terminated',
    startedAt: new Date(Date.now() - 172_800_000).toISOString(),
    readyAt: new Date(Date.now() - 172_790_000).toISOString(),
    terminatedAt: new Date(Date.now() - 43_200_000).toISOString(),
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0,
      memRequestMi: 512,
      memLimitMi: 1_024,
      memUsedMi: 0,
      gpuCount: 0,
    },
    env: {},
    events: [
      {
        ts: new Date(Date.now() - 172_800_000).toISOString(),
        kind: 'requested',
        body: 'session requested',
      },
      { ts: new Date(Date.now() - 172_790_000).toISOString(), kind: 'ready', body: 'pod ready' },
      {
        ts: new Date(Date.now() - 43_200_000).toISOString(),
        kind: 'terminated',
        body: 'TTL expired — session terminated',
      },
    ],
  },
  {
    id: 'ds-2',
    ravnId: 'r1',
    personaName: 'skald',
    sagaId: 'saga-auth',
    templateId: 'tpl-default',
    clusterId: 'cl-eitri',
    state: 'terminated',
    startedAt: '2026-03-01T10:00:00Z',
    readyAt: '2026-03-01T10:00:30Z',
    terminatedAt: '2026-03-01T11:00:00Z',
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0,
      memRequestMi: 512,
      memLimitMi: 1_024,
      memUsedMi: 0,
      gpuCount: 0,
    },
    env: {},
    events: [
      { ts: '2026-03-01T10:00:00Z', kind: 'ready', body: 'pod ready' },
      { ts: '2026-03-01T11:00:00Z', kind: 'terminated', body: 'session ended by user' },
    ],
  },
  {
    id: 'ds-3',
    ravnId: 'r2',
    personaName: 'bard',
    sagaId: 'saga-api',
    templateId: 'tpl-gpu',
    clusterId: 'cl-brokkr',
    state: 'failed',
    startedAt: '2026-03-10T08:00:00Z',
    terminatedAt: '2026-03-10T08:05:00Z',
    resources: {
      cpuRequest: 2,
      cpuLimit: 4,
      cpuUsed: 0,
      memRequestMi: 2_048,
      memLimitMi: 4_096,
      memUsedMi: 0,
      gpuCount: 1,
    },
    env: {},
    events: [{ ts: '2026-03-10T08:05:00Z', kind: 'failed', body: 'OOMKilled' }],
  },
  {
    id: 'ds-4b',
    ravnId: 'r2',
    personaName: 'bard',
    templateId: 'tpl-default',
    clusterId: 'cl-eitri',
    state: 'terminated',
    startedAt: '2026-04-01T09:00:00Z',
    readyAt: '2026-04-01T09:00:45Z',
    terminatedAt: '2026-04-01T17:00:00Z',
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0,
      memRequestMi: 512,
      memLimitMi: 1_024,
      memUsedMi: 0,
      gpuCount: 0,
    },
    env: {},
    events: [
      { ts: '2026-04-01T09:00:00Z', kind: 'ready', body: 'pod ready' },
      { ts: '2026-04-01T17:00:00Z', kind: 'terminated', body: 'idle timeout' },
    ],
  },
];

const SEED_TEMPLATES: Template[] = [
  // ── Forge-overview showcase templates (displayed first) ──────────────
  {
    id: 'tpl-platform',
    name: 'niuu-platform',
    description: 'Full niuu monorepo · all modules',
    version: 1,
    usageCount: 142,
    spec: {
      image: 'ghcr.io/niuulabs/skuld',
      tag: 'latest',
      mounts: [],
      env: {},
      envSecretRefs: [],
      tools: [],
      mcpServers: [],
      resources: {
        cpuRequest: '4',
        cpuLimit: '8',
        memRequestMi: 16_384,
        memLimitMi: 32_768,
        gpuCount: 0,
      },
      ttlSec: 3_600,
      idleTimeoutSec: 600,
    },
    createdAt: '2025-12-01T00:00:00Z',
    updatedAt: '2025-12-01T00:00:00Z',
  },
  {
    id: 'tpl-web',
    name: 'volundr-web',
    description: 'Only the web/ sub-tree · fast setup',
    version: 1,
    usageCount: 87,
    spec: {
      image: 'ghcr.io/niuulabs/skuld',
      tag: 'latest',
      mounts: [],
      env: {},
      envSecretRefs: [],
      tools: [],
      mcpServers: [],
      resources: {
        cpuRequest: '2',
        cpuLimit: '4',
        memRequestMi: 8_192,
        memLimitMi: 16_384,
        gpuCount: 0,
      },
      ttlSec: 7_200,
      idleTimeoutSec: 900,
    },
    createdAt: '2025-12-15T00:00:00Z',
    updatedAt: '2025-12-15T00:00:00Z',
  },
  {
    id: 'tpl-bifrost',
    name: 'bifrost-gateway',
    description: 'LLM gateway · provider adapters',
    version: 1,
    usageCount: 23,
    spec: {
      image: 'ghcr.io/niuulabs/skuld',
      tag: 'codex-primary',
      mounts: [
        {
          name: 'niuu/bifrost',
          mountPath: '/workspace',
          source: { kind: 'git', repo: 'niuu/bifrost', branch: 'main' },
          readOnly: false,
        },
      ],
      env: {},
      envSecretRefs: [],
      tools: [],
      mcpServers: [
        {
          name: 'filesystem',
          transport: 'stdio',
          connectionString: 'uvx mcp-filesystem',
          tools: [],
        },
        {
          name: 'git',
          transport: 'stdio',
          connectionString: 'uvx mcp-git',
          tools: [],
        },
      ],
      resources: {
        cpuRequest: '2',
        cpuLimit: '2',
        memRequestMi: 4096,
        memLimitMi: 4096,
        gpuCount: 0,
      },
      ttlSec: 3600,
      idleTimeoutSec: 600,
    },
    createdAt: '2025-12-18T00:00:00Z',
    updatedAt: '2025-12-18T00:00:00Z',
  },
  {
    id: 'tpl-mimir',
    name: 'mimir-embeddings',
    description: 'Indexer · needs GPU for local embeds',
    version: 1,
    usageCount: 14,
    spec: {
      image: 'ghcr.io/niuulabs/skuld',
      tag: 'latest',
      mounts: [],
      env: {},
      envSecretRefs: [],
      tools: [],
      mcpServers: [],
      resources: {
        cpuRequest: '4',
        cpuLimit: '4',
        memRequestMi: 32_768,
        memLimitMi: 32_768,
        gpuCount: 1,
      },
      ttlSec: 5400,
      idleTimeoutSec: 900,
    },
    createdAt: '2025-12-20T00:00:00Z',
    updatedAt: '2025-12-20T00:00:00Z',
  },
  {
    id: 'tpl-scratch',
    name: 'scratch',
    description: 'Blank workspace · ad hoc shell',
    version: 1,
    usageCount: 12,
    spec: {
      image: 'ghcr.io/niuulabs/skuld',
      tag: 'latest',
      mounts: [],
      env: {},
      envSecretRefs: [],
      tools: [],
      mcpServers: [],
      resources: {
        cpuRequest: '1',
        cpuLimit: '2',
        memRequestMi: 1024,
        memLimitMi: 2048,
        gpuCount: 0,
      },
      ttlSec: 3600,
      idleTimeoutSec: 600,
    },
    createdAt: '2025-12-22T00:00:00Z',
    updatedAt: '2025-12-22T00:00:00Z',
  },
  {
    id: 'tpl-local',
    name: 'local-laptop',
    description: 'Laptop bridge · local runtime shell',
    version: 1,
    usageCount: 9,
    spec: {
      image: 'ghcr.io/niuulabs/skuld',
      tag: 'latest',
      mounts: [],
      env: {},
      envSecretRefs: [],
      tools: [],
      mcpServers: [],
      resources: {
        cpuRequest: '1',
        cpuLimit: '2',
        memRequestMi: 2048,
        memLimitMi: 4096,
        gpuCount: 0,
      },
      ttlSec: 3600,
      idleTimeoutSec: 600,
    },
    createdAt: '2025-12-23T00:00:00Z',
    updatedAt: '2025-12-23T00:00:00Z',
  },
  // ── Original templates (other tests depend on these) ─────────────────
  {
    id: 'tpl-default',
    name: 'default',
    description: 'Minimal forge template — standard skuld image with no extra tooling.',
    version: 1,
    usageCount: 42,
    spec: {
      image: 'ghcr.io/niuulabs/skuld',
      tag: 'latest',
      mounts: [],
      env: {},
      envSecretRefs: [],
      tools: [],
      mcpServers: [],
      resources: {
        cpuRequest: '1',
        cpuLimit: '2',
        memRequestMi: 512,
        memLimitMi: 1_024,
        gpuCount: 0,
      },
      ttlSec: 3_600,
      idleTimeoutSec: 600,
    },
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
  },
  {
    id: 'tpl-gpu',
    name: 'gpu-workload',
    description: 'GPU-accelerated research template with Python and Jupyter via CUDA 12.',
    version: 2,
    usageCount: 7,
    spec: {
      image: 'ghcr.io/niuulabs/skuld',
      tag: 'cuda-12',
      mounts: [],
      env: { MODEL_PATH: '/models' },
      envSecretRefs: ['HF_TOKEN'],
      tools: ['python', 'jupyter'],
      mcpServers: [
        {
          name: 'python',
          transport: 'stdio',
          connectionString: 'uvx mcp-python',
          tools: ['run_script', 'install_package', 'read_file'],
        },
        {
          name: 'jupyter',
          transport: 'stdio',
          connectionString: 'uvx mcp-jupyter',
          tools: ['execute_cell', 'list_kernels', 'create_notebook'],
        },
      ],
      resources: {
        cpuRequest: '2',
        cpuLimit: '4',
        memRequestMi: 4_096,
        memLimitMi: 8_192,
        gpuCount: 1,
      },
      ttlSec: 7_200,
      idleTimeoutSec: 900,
      clusterAffinity: ['cl-eitri'],
    },
    createdAt: '2026-02-01T00:00:00Z',
    updatedAt: '2026-03-15T00:00:00Z',
  },
];

const SEED_SESSION_DEFINITIONS: SessionDefinition[] = [
  {
    key: 'skuld-claude',
    displayName: 'Claude Code',
    description: 'Anthropic Claude-powered coding agent with full tool access.',
    labels: ['anthropic', 'coding'],
    defaultModel: 'sonnet-primary',
  },
  {
    key: 'skuld-codex',
    displayName: 'Codex',
    description: 'OpenAI Codex-powered coding agent for autonomous tasks.',
    labels: ['openai', 'coding'],
    defaultModel: 'gpt-5-codex',
  },
  {
    key: 'skuld-gemini',
    displayName: 'Gemini',
    description: 'Google Gemini-powered coding agent with multimodal support.',
    labels: ['google', 'coding', 'multimodal'],
    defaultModel: 'gemini-primary',
  },
  {
    key: 'skuld-aider',
    displayName: 'Aider',
    description: 'Aider CLI coding assistant — model-agnostic pair programmer.',
    labels: ['open-source', 'coding'],
    defaultModel: 'sonnet-primary',
  },
];

// ---------------------------------------------------------------------------
// IVolundrService mock
// ---------------------------------------------------------------------------

export function createMockVolundrService(): IVolundrService {
  const sessions = [...SEED_SESSIONS];
  const credentials = new Map(SEED_CREDENTIALS.map((credential) => [credential.name, credential]));
  const presets = [...SEED_PRESETS];

  return {
    getFeatures: async () => ({
      localMountsEnabled: false,
      fileManagerEnabled: true,
      miniMode: false,
    }),

    getSessionDefinitions: async () => [...SEED_SESSION_DEFINITIONS],

    getSessions: async () => sessions,

    getSession: async (id) => sessions.find((s) => s.id === id) ?? null,

    getActiveSessions: async () =>
      sessions.filter((s) => ['starting', 'provisioning', 'running'].includes(s.status)),

    getStats: async () => ({ ...SEED_STATS }),

    getModels: async () => ({ ...SEED_MODELS }),

    getRepos: async () => [...SEED_REPOS],

    subscribe: (callback) => {
      callback(sessions);
      return () => {};
    },

    subscribeStats: (_callback) => () => {},

    getTemplates: async () =>
      SEED_TEMPLATES.map((t) => ({
        name: t.name,
        description: '',
        isDefault: t.id === 'tpl-default',
        repos: [],
        setupScripts: [],
        workspaceLayout: {},
        cliTool: 'claude',
        workloadType: 'skuld-claude',
        model: null,
        systemPrompt: null,
        resourceConfig: {
          cpu: t.spec.resources.cpuRequest,
          memory: String(t.spec.resources.memRequestMi),
          gpu: String(t.spec.resources.gpuCount),
        },
        mcpServers: [],
        envVars: t.spec.env,
        envSecretRefs: t.spec.envSecretRefs,
        workloadConfig: {},
        terminalSidecar: { enabled: false, allowedCommands: [] },
        skills: [],
        rules: [],
      })),
    getTemplate: async (name) => {
      const t = SEED_TEMPLATES.find((tpl) => tpl.name === name);
      if (!t) return null;
      return {
        name: t.name,
        description: '',
        isDefault: t.id === 'tpl-default',
        repos: [],
        setupScripts: [],
        workspaceLayout: {},
        cliTool: 'claude',
        workloadType: 'skuld-claude',
        model: null,
        systemPrompt: null,
        resourceConfig: { cpu: t.spec.resources.cpuRequest },
        mcpServers: [],
        envVars: t.spec.env,
        envSecretRefs: t.spec.envSecretRefs,
        workloadConfig: {},
        terminalSidecar: { enabled: false, allowedCommands: [] },
        skills: [],
        rules: [],
      };
    },
    saveTemplate: async (t) => t,

    getPresets: async () => [...presets],
    getPreset: async (id) => presets.find((preset) => preset.id === id) ?? null,
    savePreset: async (p) => {
      const saved = {
        ...p,
        id: p.id ?? `preset-${Date.now()}`,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      };
      const existingIndex = presets.findIndex((preset) => preset.id === saved.id);
      if (existingIndex >= 0) {
        presets[existingIndex] = saved;
      } else {
        presets.push(saved);
      }
      return saved;
    },
    deletePreset: async () => {},

    getAvailableMcpServers: async () => [...SEED_MCP_SERVERS],
    getAvailableSecrets: async () => [],
    createSecret: async (name) => ({ name, keys: [] }),
    getClusterResources: async () => SEED_CLUSTER_RESOURCES,

    startSession: async (config) => ({
      id: 'sess-new',
      name: config.name,
      source: config.source,
      status: 'starting',
      model: config.model,
      lastActive: Date.now(),
      messageCount: 0,
      tokensUsed: 0,
    }),

    connectSession: async (config) => ({
      id: 'sess-ext',
      name: config.name,
      source: { type: 'git', repo: config.hostname, branch: 'main' },
      status: 'running',
      model: 'unknown',
      lastActive: Date.now(),
      messageCount: 0,
      tokensUsed: 0,
      origin: 'manual',
      hostname: config.hostname,
    }),

    updateSession: async (_id, updates) => ({ ...sessions[0]!, ...updates }),

    stopSession: async () => {},
    resumeSession: async () => {},
    deleteSession: async () => {},
    archiveSession: async () => {},
    restoreSession: async () => {},
    listArchivedSessions: async () => [],

    getMessages: async () => [],
    sendMessage: async (_sessionId, content): Promise<VolundrMessage> => ({
      id: `msg-${Date.now()}`,
      sessionId: _sessionId,
      role: 'assistant',
      content: `echo: ${content}`,
      timestamp: Date.now(),
    }),
    subscribeMessages: () => () => {},

    getLogs: async () => [],
    subscribeLogs: () => () => {},
    getAggregatedLogs: async () => ({ lines: [], participants: [] }),
    subscribeAggregatedLogs: () => () => {},

    getCodeServerUrl: async () => null,

    getChronicle: async () => null,
    subscribeChronicle: () => () => {},

    getPullRequests: async () => [],
    createPullRequest: async (_sessionId, title = 'Draft PR') => ({
      number: 1,
      title,
      url: 'https://github.com/niuulabs/volundr/pull/1',
      repoUrl: 'github.com/niuulabs/volundr',
      provider: 'github',
      sourceBranch: 'feat/new',
      targetBranch: 'main',
      status: 'open',
    }),
    mergePullRequest: async () => ({ merged: true }),
    getCIStatus: async () => 'unknown',

    getSessionMcpServers: async () => [],

    searchTrackerIssues: async (query) =>
      SEED_TRACKER_ISSUES.filter(
        (issue) =>
          issue.identifier.toLowerCase().includes(query.toLowerCase()) ||
          issue.title.toLowerCase().includes(query.toLowerCase()),
      ),
    getProjectRepoMappings: async () => [],
    updateTrackerIssueStatus: async (issueId, status) => ({
      id: issueId,
      identifier: 'NIU-?',
      title: 'mock issue',
      status,
      url: '',
    }),

    getIdentity: async () => ({
      userId: 'u1',
      email: 'dev@niuu.world',
      tenantId: 't1',
      roles: ['user'],
      displayName: 'Dev',
      status: 'active',
    }),

    listUsers: async () => [],

    getTenants: async () => [],
    getTenant: async () => null,
    createTenant: async (data) => ({
      id: `tenant-${Date.now()}`,
      path: `/${data.name}`,
      name: data.name,
      tier: data.tier,
      maxSessions: data.maxSessions,
      maxStorageGb: data.maxStorageGb,
    }),
    deleteTenant: async () => {},
    updateTenant: async (id, data) => ({
      id,
      path: `/${id}`,
      name: id,
      tier: data.tier ?? 'free',
      maxSessions: data.maxSessions ?? 5,
      maxStorageGb: data.maxStorageGb ?? 10,
    }),
    getTenantMembers: async () => [],
    reprovisionUser: async (userId) => ({ success: true, userId, errors: [] }),
    reprovisionTenant: async () => [],

    getUserCredentials: async () => [],
    storeUserCredential: async () => {},
    deleteUserCredential: async () => {},
    getTenantCredentials: async () => [],
    storeTenantCredential: async () => {},
    deleteTenantCredential: async () => {},

    getIntegrationCatalog: async () => [],
    getIntegrations: async () => [...SEED_INTEGRATIONS],
    createIntegration: async () => ({
      id: `int-${Date.now()}`,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    }),
    deleteIntegration: async () => {},
    testIntegration: async () => ({ success: true }),

    getCredentials: async (type?: SecretType): Promise<StoredCredential[]> => {
      const rows = Array.from(credentials.values());
      return type ? rows.filter((credential) => credential.secretType === type) : rows;
    },
    getCredential: async (name) => credentials.get(name) ?? null,
    createCredential: async (req) => {
      const created = {
        id: `cred-${Date.now()}`,
        name: req.name,
        secretType: req.secretType,
        keys: Object.keys(req.data),
        scope: 'global',
        used: 0,
        metadata: req.metadata ?? {},
        createdAt: new Date().toISOString(),
        updatedAt: 'just now',
      };
      credentials.set(created.name, created);
      return created;
    },
    deleteCredential: async (name) => {
      credentials.delete(name);
    },
    getCredentialTypes: async () => [],

    listWorkspaces: async (status) =>
      status
        ? SEED_WORKSPACES.filter((workspace) => workspace.status === status)
        : [...SEED_WORKSPACES],
    listAllWorkspaces: async () => [],
    restoreWorkspace: async () => {},
    deleteWorkspace: async () => {},
    bulkDeleteWorkspaces: async () => ({ deleted: 0, failed: [] }),

    getAdminSettings: async () => ({
      storage: { homeEnabled: false, fileManagerEnabled: false },
    }),
    updateAdminSettings: async () => ({
      storage: { homeEnabled: false, fileManagerEnabled: false },
    }),

    getFeatureModules: async (): Promise<FeatureModule[]> => [],
    toggleFeature: async (key, enabled): Promise<FeatureModule> => ({
      key,
      label: key,
      icon: '',
      scope: 'user',
      enabled,
      defaultEnabled: false,
      adminOnly: false,
      order: 0,
    }),
    getUserFeaturePreferences: async () => [],
    updateUserFeaturePreferences: async (prefs) => prefs,

    listTokens: async () => [],
    createToken: async (name) => ({
      id: `pat-${Date.now()}`,
      name,
      token: 'mock-pat-token',
      createdAt: new Date().toISOString(),
    }),
    revokeToken: async () => {},
  };
}

// ---------------------------------------------------------------------------
// IClusterAdapter mock
// ---------------------------------------------------------------------------

export function createMockClusterAdapter(): IClusterAdapter {
  const clusters = [...SEED_CLUSTERS];
  return {
    getClusters: async () => clusters,
    getCluster: async (id) => clusters.find((c) => c.id === id) ?? null,
  };
}

// ---------------------------------------------------------------------------
// ISessionStore mock
// ---------------------------------------------------------------------------

export function createMockSessionStore(): ISessionStore {
  let sessions = [...SEED_DOMAIN_SESSIONS];
  const listeners: Array<(sessions: Session[]) => void> = [];

  function notify() {
    for (const cb of listeners) cb(sessions);
  }

  return {
    getSession: async (id) => sessions.find((s) => s.id === id) ?? null,

    listSessions: async (filters) => {
      if (!filters) return sessions;
      return sessions.filter((s) => {
        if (filters.state && s.state !== filters.state) return false;
        if (filters.clusterId && s.clusterId !== filters.clusterId) return false;
        if (filters.ravnId && s.ravnId !== filters.ravnId) return false;
        return true;
      });
    },

    createSession: async (spec) => {
      const session: Session = {
        ...spec,
        id: `ds-${Date.now()}`,
        events: [],
      };
      sessions = [...sessions, session];
      notify();
      return session;
    },

    updateSession: async (id, updates) => {
      const idx = sessions.findIndex((s) => s.id === id);
      if (idx === -1) throw new Error(`Session not found: ${id}`);
      const updated = { ...sessions[idx]!, ...updates };
      sessions = sessions.map((s) => (s.id === id ? updated : s));
      notify();
      return updated;
    },

    deleteSession: async (id) => {
      sessions = sessions.filter((s) => s.id !== id);
      notify();
    },

    subscribe: (callback) => {
      listeners.push(callback);
      callback(sessions);
      return () => {
        const i = listeners.indexOf(callback);
        if (i !== -1) listeners.splice(i, 1);
      };
    },
  };
}

// ---------------------------------------------------------------------------
// ITemplateStore mock
// ---------------------------------------------------------------------------

export function createMockTemplateStore(): ITemplateStore {
  let templates = [...SEED_TEMPLATES];
  return {
    getTemplate: async (id) => templates.find((t) => t.id === id) ?? null,
    listTemplates: async () => templates,
    createTemplate: async (name, spec) => {
      const t: Template = {
        id: `tpl-${Date.now()}`,
        name,
        version: 1,
        spec,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      };
      templates = [...templates, t];
      return t;
    },
    updateTemplate: async (id, spec) => {
      const existing = templates.find((t) => t.id === id);
      if (!existing) throw new Error(`Template not found: ${id}`);
      const updated = {
        ...existing,
        spec,
        version: existing.version + 1,
        updatedAt: new Date().toISOString(),
      };
      templates = templates.map((t) => (t.id === id ? updated : t));
      return updated;
    },
    deleteTemplate: async (id) => {
      templates = templates.filter((t) => t.id !== id);
    },
  };
}

// ---------------------------------------------------------------------------
// IPtyStream mock
// ---------------------------------------------------------------------------

export function createMockPtyStream(): IPtyStream {
  const subscribers = new Map<string, Array<(chunk: string) => void>>();
  const buffers = new Map<string, string>();

  function notify(sessionId: string, chunk: string) {
    for (const cb of subscribers.get(sessionId) ?? []) cb(chunk);
  }

  function buildTranscript(sessionId: string): string {
    const [baseSessionId, terminalId = 'main'] = sessionId.split('::');

    if (baseSessionId === 'laptop-volundr-local' && terminalId === 'main') {
      return [
        'workspace $ git log --oneline -5',
        'a019be2 perf: throttle pan to rAF',
        'f2b9c1a perf: quadtree cull @ 60fps',
        'c44001d chore: add perf test harness',
        '7b2adb0 obs: viewport-box math',
        '1e7bc22 obs: initial canvas skeleton',
        'workspace $ git diff --stat HEAD~1',
        ' observatory.jsx   | 248 ++++++++++++++++++++++++----',
        ' canvas/quadtree.js |  96 +++++++++',
        ' styles.css        |  10 +--',
        ' 3 files changed, 340 insertions(+), 14 deletions(-)',
        'workspace $ ',
      ].join('\r\n');
    }
    if (baseSessionId === 'laptop-volundr-local' && terminalId === 'tests') {
      return [
        'tests $ pnpm test observatory.perf.test.ts',
        ' PASS  observatory.perf.test.ts',
        '  quadtree culls off-screen entities',
        '  reduced motion pan stays under 8ms',
        '',
        ' Test Files  1 passed (1)',
        'tests $ ',
      ].join('\r\n');
    }
    if (baseSessionId === 'laptop-volundr-local' && terminalId === 'view-io') {
      return [
        'view $ jq . metrics/render.json',
        '{',
        '  "sast_findings": 0,',
        '  "deps_changed": 0,',
        '  "scope": "frontend-only"',
        '}',
        'view $ ',
      ].join('\r\n');
    }
    if (baseSessionId === 'laptop-volundr-local') {
      return [`${terminalId} $ `].join('\r\n');
    }
    return '$ ';
  }

  function ensureBuffer(sessionId: string): string {
    const existing = buffers.get(sessionId);
    if (existing !== undefined) return existing;
    const seeded = buildTranscript(sessionId);
    buffers.set(sessionId, seeded);
    return seeded;
  }

  return {
    subscribe: (sessionId, onData) => {
      const existing = subscribers.get(sessionId) ?? [];
      existing.push(onData);
      subscribers.set(sessionId, existing);
      setTimeout(() => {
        notify(sessionId, ensureBuffer(sessionId));
      }, 50);
      return () => {
        const updated = (subscribers.get(sessionId) ?? []).filter((cb) => cb !== onData);
        subscribers.set(sessionId, updated);
      };
    },
    send: (sessionId, data) => {
      if (data === '\r') {
        const chunk = '\r\nmock-output\r\n$ ';
        const next = `${ensureBuffer(sessionId)}${chunk}`;
        buffers.set(sessionId, next);
        notify(sessionId, chunk);
        return;
      }
      const next = `${ensureBuffer(sessionId)}${data}`;
      buffers.set(sessionId, next);
      notify(sessionId, data);
    },
  };
}

// ---------------------------------------------------------------------------
// IFileSystemPort mock
// ---------------------------------------------------------------------------

const SEED_FILE_TREE: FileTreeNode[] = [
  {
    name: 'src',
    path: '/workspace/src',
    kind: 'directory',
    children: [
      { name: 'index.ts', path: '/workspace/src/index.ts', kind: 'file', size: 512 },
      { name: 'app.tsx', path: '/workspace/src/app.tsx', kind: 'file', size: 1_024 },
    ],
  },
  { name: 'package.json', path: '/workspace/package.json', kind: 'file', size: 800 },
  { name: 'README.md', path: '/workspace/README.md', kind: 'file', size: 2_048 },
  {
    name: 'env',
    path: '/mnt/secrets',
    kind: 'directory',
    mountName: 'api-secrets',
    isSecret: true,
    children: [
      {
        name: 'API_KEY',
        path: '/mnt/secrets/API_KEY',
        kind: 'file',
        isSecret: true,
        mountName: 'api-secrets',
      },
    ],
  },
];

const SEED_FILE_CONTENTS: Record<string, string> = {
  '/workspace/src/index.ts': `import { App } from './app';\n\nconst app = new App();\napp.listen(8080);\n`,
  '/workspace/src/app.tsx': `import React from 'react';\n\nexport function App() {\n  return <div>Hello from the dev pod!</div>;\n}\n`,
  '/workspace/package.json': `{\n  "name": "mock-project",\n  "version": "0.0.1",\n  "type": "module"\n}\n`,
  '/workspace/README.md': `# Mock project\n\nThis is a mock workspace generated by the Völundr mock adapter.\n`,
};

export function createMockFileSystemPort(): IFileSystemPort {
  let tree = structuredClone(SEED_FILE_TREE);
  const contents = { ...SEED_FILE_CONTENTS };

  function findNode(nodes: FileTreeNode[], target: string): FileTreeNode | null {
    for (const node of nodes) {
      if (node.path === target) return node;
      if (node.children) {
        const found = findNode(node.children, target);
        if (found) return found;
      }
    }
    return null;
  }

  function findParent(nodes: FileTreeNode[], target: string): FileTreeNode | null {
    for (const node of nodes) {
      if (node.children?.some((child) => child.path === target)) return node;
      if (node.children) {
        const found = findParent(node.children, target);
        if (found) return found;
      }
    }
    return null;
  }

  function removePath(target: string) {
    const parent = findParent(tree, target);
    if (parent?.children) {
      parent.children = parent.children.filter((child) => child.path !== target);
      return;
    }
    tree = tree.filter((node) => node.path !== target);
  }

  return {
    listTree: async (_sessionId) => tree,

    expandDirectory: async (_sessionId, path) => {
      return findNode(tree, path)?.children ?? [];
    },

    readFile: async (_sessionId, path) => {
      const content = contents[path];
      if (!content) throw new Error(`File not found: ${path}`);
      return content;
    },

    writeFile: async (_sessionId, path, content) => {
      contents[path] = content;
      const existing = findNode(tree, path);
      if (existing) {
        existing.size = content.length;
        return;
      }
      const parentPath = path.slice(0, path.lastIndexOf('/'));
      const parent = findNode(tree, parentPath);
      const newNode: FileTreeNode = {
        name: path.split('/').at(-1) ?? path,
        path,
        kind: 'file',
        size: content.length,
      };
      if (parent?.children) {
        parent.children = [...parent.children, newNode].sort((a, b) =>
          a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === 'directory' ? -1 : 1,
        );
      } else {
        tree = [...tree, newNode];
      }
    },

    deletePaths: async (_sessionId, paths) => {
      for (const path of paths) {
        delete contents[path];
        removePath(path);
      }
    },
  };
}

// ---------------------------------------------------------------------------
// IMetricsStream mock
// ---------------------------------------------------------------------------

export function createMockMetricsStream(): IMetricsStream {
  return {
    subscribe: (_sessionId, onMetrics) => {
      onMetrics({ timestamp: Date.now(), cpu: 0.4, memMi: 320, gpu: 0 });
      const interval = setInterval(
        () =>
          onMetrics({
            timestamp: Date.now(),
            cpu: Math.random(),
            memMi: 300 + Math.random() * 100,
            gpu: 0,
          }),
        2_000,
      );
      return () => clearInterval(interval);
    },
  };
}
