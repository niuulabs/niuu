/**
 * Mock adapters for all Ravn ports.
 *
 * Seeded from the built-in persona definitions that mirror
 * the backend YAML files in src/ravn/personas/.
 */

import type {
  IPersonaStore,
  IRavenStream,
  ISessionStream,
  ITriggerStore,
  IBudgetStream,
  IWardenStore,
  PersonaSummary,
  PersonaDetail,
  PersonaCreateRequest,
  PersonaForkRequest,
  PersonaFilter,
  WardenSummary,
  WardenCreateRequest,
} from '../ports';
import type { Ravn } from '../domain/ravn';
import type { Session } from '../domain/session';
import type { Trigger } from '../domain/trigger';
import type { Message } from '../domain/message';
import type { BudgetState } from '@niuulabs/domain';

// ---------------------------------------------------------------------------
// Seed data — mirrors web/src/modules/ravn/api/mockData.ts
// ---------------------------------------------------------------------------

const SEED_PERSONAS: PersonaSummary[] = [
  {
    name: 'architect',
    role: 'plan',
    letter: 'A',
    color: 'var(--color-accent-cyan)',
    summary: 'High-level design and planning persona.',
    permissionMode: 'default',
    allowedTools: ['read', 'web', 'mimir.read', 'ravn.dispatch'],
    iterationBudget: 25,
    isBuiltin: true,
    hasOverride: true,
    producesEvent: 'plan.completed',
    consumesEvents: ['code.requested', 'feature.requested'],
  },
  {
    name: 'autonomous-agent',
    role: 'autonomy',
    letter: 'A',
    color: 'var(--color-accent-purple)',
    summary: 'Fully autonomous general-purpose agent.',
    permissionMode: 'loose',
    allowedTools: [],
    iterationBudget: 100,
    isBuiltin: true,
    hasOverride: true,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'coder',
    role: 'build',
    letter: 'C',
    color: 'var(--color-accent-indigo)',
    summary: 'Writes and edits source code.',
    permissionMode: 'default',
    allowedTools: ['read', 'write', 'git.status', 'bash', 'ravn.dispatch'],
    iterationBudget: 40,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'code.changed',
    consumesEvents: [
      'review.changes_requested',
      'security.changes_requested',
      'code.requested',
      'bug.fix.requested',
      'feature.requested',
    ],
  },
  {
    name: 'coordinator',
    role: 'coord',
    letter: 'C',
    color: 'var(--color-accent-amber)',
    summary: 'Orchestrates multi-step workflows.',
    permissionMode: 'default',
    allowedTools: ['ravn.cascade', 'read', 'ravn.dispatch'],
    iterationBudget: 30,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'draft-a-note',
    role: 'write',
    letter: 'D',
    color: 'var(--color-accent-emerald)',
    summary: 'Drafts concise notes and summaries.',
    permissionMode: 'safe',
    allowedTools: ['read', 'web', 'mimir.read', 'ravn.dispatch'],
    iterationBudget: 15,
    isBuiltin: true,
    hasOverride: true,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'health-auditor',
    role: 'observe',
    letter: 'H',
    color: 'var(--color-accent-cyan)',
    summary: 'Periodically audits system health metrics.',
    permissionMode: 'safe',
    allowedTools: ['read', 'bash', 'web', 'ravn.dispatch'],
    iterationBudget: 20,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'health.completed',
    consumesEvents: ['health.check.requested', 'cron.hourly'],
  },
  {
    name: 'investigator',
    role: 'investigate',
    letter: 'I',
    color: 'var(--color-accent-amber)',
    summary: 'Root-cause analysis for incidents and bugs.',
    permissionMode: 'default',
    allowedTools: ['read', 'git.log', 'bash', 'web', 'ravn.dispatch'],
    iterationBudget: 40,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'investigation.completed',
    consumesEvents: ['bug.reported', 'incident.opened', 'qa.failed'],
  },
  {
    name: 'mimir-curator',
    role: 'knowledge',
    letter: 'M',
    color: 'var(--color-accent-purple)',
    summary: 'Curates and indexes knowledge into Mímir.',
    permissionMode: 'safe',
    allowedTools: ['read', 'mimir.write', 'ravn.dispatch'],
    iterationBudget: 20,
    isBuiltin: true,
    hasOverride: true,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'mimir-warden',
    role: 'knowledge',
    letter: 'W',
    color: 'var(--color-accent-purple)',
    summary: 'Long-lived Mimir warden for curation, refresh, and dream-cycle maintenance.',
    permissionMode: 'default',
    allowedTools: ['read', 'web', 'mimir.read', 'mimir.write', 'ravn.dispatch'],
    iterationBudget: 60,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'mimir.warden.completed',
    consumesEvents: [
      'mimir.source.ingested',
      'mimir.page.stale',
      'mimir.dream.requested',
      'cron.weekly',
    ],
  },
  {
    name: 'planning-agent',
    role: 'plan',
    letter: 'P',
    color: 'var(--color-accent-cyan)',
    summary: 'Decomposes goals into actionable plans.',
    permissionMode: 'safe',
    allowedTools: ['read', 'web', 'mimir.read', 'ravn.dispatch'],
    iterationBudget: 25,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'product-steward',
    role: 'plan',
    letter: 'P',
    color: 'var(--color-accent-cyan)',
    summary: 'Long-lived product steward for chat, memory, and workflow launch.',
    permissionMode: 'read-only',
    allowedTools: ['mimir', 'volundr_session', 'ting_workflow', 'ting_plan', 'ting_spec'],
    iterationBudget: 0,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [
      'research.completed',
      'research.failed',
      'spec.completed',
      'plan.completed',
      'delivery.completed',
    ],
  },
  {
    name: 'qa-agent',
    role: 'qa',
    letter: 'Q',
    color: 'var(--color-accent-amber)',
    summary: 'Runs test suites and validates code quality.',
    permissionMode: 'default',
    allowedTools: ['read', 'git.status', 'bash', 'ravn.dispatch'],
    iterationBudget: 30,
    isBuiltin: true,
    hasOverride: true,
    producesEvent: 'qa.completed',
    consumesEvents: ['review.completed', 'test.requested'],
  },
  {
    name: 'reporter',
    role: 'report',
    letter: 'R',
    color: 'var(--color-accent-emerald)',
    summary: 'Produces status reports and summaries.',
    permissionMode: 'safe',
    allowedTools: ['read', 'mimir.read', 'ravn.dispatch'],
    iterationBudget: 15,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'retro-analyst',
    role: 'observe',
    letter: 'R',
    color: 'var(--color-accent-cyan)',
    summary: 'Runs retrospective analysis on completed work.',
    permissionMode: 'safe',
    allowedTools: ['read', 'mimir.read', 'ravn.dispatch'],
    iterationBudget: 20,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'review-arbiter',
    role: 'arbiter',
    letter: 'R',
    color: 'var(--color-accent-indigo)',
    summary: 'Final arbiter for contested code reviews.',
    permissionMode: 'safe',
    allowedTools: ['read', 'git.log', 'web', 'ravn.dispatch'],
    iterationBudget: 25,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'review.completed',
    consumesEvents: ['review.requested'],
  },
  {
    name: 'reviewer',
    role: 'review',
    letter: 'R',
    color: 'var(--color-accent-indigo)',
    summary: 'Reviews code changes and provides feedback.',
    permissionMode: 'safe',
    allowedTools: ['read', 'git.log', 'web', 'ravn.dispatch'],
    iterationBudget: 25,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'review.completed',
    consumesEvents: ['code.changed', 'review.requested'],
  },
  {
    name: 'security-auditor',
    role: 'review',
    letter: 'S',
    color: 'var(--color-accent-red)',
    summary: 'Periodic deep security audits.',
    permissionMode: 'safe',
    allowedTools: ['read', 'bash', 'web', 'ravn.dispatch'],
    iterationBudget: 30,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
  },
  {
    name: 'verifier',
    role: 'qa',
    letter: 'V',
    color: 'var(--color-accent-amber)',
    summary: 'Holistic verification across code, tests, and docs.',
    permissionMode: 'default',
    allowedTools: ['read', 'git.status', 'bash', 'web', 'ravn.dispatch'],
    iterationBudget: 30,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'verification.completed',
    consumesEvents: ['qa.completed', 'code.changed', 'verification.requested'],
  },
];

const DEFAULT_WARDEN_FEATURES: WardenSummary['features'] = {
  wakefulnessEnabled: true,
  dreamCycleEnabled: true,
  threadQueueEnabled: true,
  threadEnricherEnabled: true,
  recapEnabled: true,
  sourceTriggerEnabled: true,
  stalenessTriggerEnabled: true,
};

const DEFAULT_WARDEN_SCHEDULES: WardenSummary['schedules'] = {
  dreamCycleCronExpression: '0 3 * * *',
  dreamCyclePollIntervalSeconds: 60,
  sourceTriggerPollIntervalSeconds: 60,
  stalenessTriggerScheduleHours: 6,
};

const DEFAULT_WARDEN_CONSOLE: WardenSummary['console'] = {
  enabled: true,
  host: '0.0.0.0',
  port: 8400,
  publicHost: '',
  authMode: 'noop',
};

function defaultSupervisor(
  id: string,
  installed: boolean,
  runtimeState: 'active' | 'idle' | 'offline',
  deployment: WardenSummary['deployment'] = 'launchd',
): WardenSummary['supervisor'] {
  const configFile = `/Users/jozefvaneenbergen/.ravn/wardens/${id}/config.yaml`;
  const isKubernetes = deployment.startsWith('k8s');
  const serviceFile = isKubernetes
    ? `/Users/jozefvaneenbergen/.ravn/wardens/${id}/k8s-bundle.yaml`
    : `/Users/jozefvaneenbergen/.ravn/wardens/${id}/warden.${deployment === 'systemd' ? 'service' : 'plist'}`;
  const startCommand = isKubernetes ? deployment : `ravn daemon --config ${configFile}`;
  return {
    installed,
    serviceLabel: installed ? `dev.niuu.ravn.warden.${id}` : '',
    serviceFile: installed ? serviceFile : '',
    configFile: installed ? configFile : '',
    startCommand: installed ? startCommand : '',
    stdoutLog: installed ? `/Users/jozefvaneenbergen/.ravn/wardens/${id}/warden.log` : '',
    stderrLog: installed ? `/Users/jozefvaneenbergen/.ravn/wardens/${id}/warden.error.log` : '',
    lastInstallAt: installed && runtimeState !== 'offline' ? '2026-04-19T02:30:00Z' : undefined,
    observation: installed
      ? {
          status: runtimeState === 'active' ? 'running' : 'idle',
          detail:
            runtimeState === 'active'
              ? 'Mock backend reports the warden as running'
              : 'Mock backend reports the warden as installed but idle',
          source: deployment.startsWith('k8s') ? 'mock-kubernetes' : 'mock-supervisor',
          checkedAt: '2026-04-19T03:10:00Z',
          fields: [],
        }
      : {
          status: 'missing',
          detail: 'Mock backend reports no installed deployment',
          source: deployment.startsWith('k8s') ? 'mock-kubernetes' : 'mock-supervisor',
          checkedAt: '2026-04-19T03:10:00Z',
          fields: [],
        },
  };
}

const SEED_WARDENS: WardenSummary[] = [
  {
    id: 'ravn-fjolnir',
    name: 'Ravn Fjolnir',
    persona: 'research-and-distill',
    profile: 'infra-synthesis',
    deployment: 'launchd',
    deploymentKwargs: {},
    model: 'claude-sonnet-4-6',
    mountNames: ['local', 'shared', 'platform'],
    writeMount: 'local',
    readMountNames: ['local', 'shared', 'platform'],
    writeMountNames: ['local'],
    categoryScope: ['infra', 'api', 'arch'],
    features: DEFAULT_WARDEN_FEATURES,
    schedules: DEFAULT_WARDEN_SCHEDULES,
    console: DEFAULT_WARDEN_CONSOLE,
    autostart: true,
    createdAt: '2026-04-15T10:00:00Z',
    createdBy: 'operator',
    runtime: {
      state: 'active',
      pagesTouched: 52,
      lastStartedAt: '2026-04-19T03:05:00Z',
      lastDream: {
        id: 'dream-001',
        timestamp: '2026-04-19T03:00:00Z',
        ravn: 'ravn-fjolnir',
        mounts: ['local', 'shared'],
        pagesUpdated: 8,
        entitiesCreated: 2,
        lintFixes: 1,
        durationMs: 42000,
      },
    },
    supervisor: defaultSupervisor('ravn-fjolnir', true, 'active', 'launchd'),
    operator: {
      rune: 'ᚠ',
      role: 'index',
      bio: 'Synthesises infrastructure documentation from git commits and runbooks',
      expertise: ['infra', 'api', 'arch'],
      tools: ['mimir', 'web', 'file', 'ravn'],
    },
  },
  {
    id: 'ravn-skald',
    name: 'Ravn Skald',
    persona: 'draft-a-note',
    profile: 'api-distillation',
    deployment: 'launchd',
    deploymentKwargs: {},
    model: 'gpt-5.5',
    mountNames: ['shared', 'platform'],
    writeMount: 'shared',
    readMountNames: ['shared', 'platform'],
    writeMountNames: ['shared'],
    categoryScope: ['api', 'observability'],
    features: DEFAULT_WARDEN_FEATURES,
    schedules: DEFAULT_WARDEN_SCHEDULES,
    console: DEFAULT_WARDEN_CONSOLE,
    autostart: false,
    createdAt: '2026-04-15T10:05:00Z',
    createdBy: 'operator',
    runtime: {
      state: 'idle',
      pagesTouched: 28,
      lastStartedAt: '2026-04-18T03:10:00Z',
      lastDream: {
        id: 'dream-003',
        timestamp: '2026-04-17T03:00:00Z',
        ravn: 'ravn-skald',
        mounts: ['platform'],
        pagesUpdated: 6,
        entitiesCreated: 1,
        lintFixes: 0,
        durationMs: 28000,
      },
    },
    supervisor: defaultSupervisor('ravn-skald', true, 'idle', 'launchd'),
    operator: {
      rune: 'ᛋ',
      role: 'build',
      bio: 'Compiles API guidelines and architectural decisions from RFC discussions',
      expertise: ['api', 'observability'],
      tools: ['mimir', 'web'],
    },
  },
  {
    id: 'ravn-galdra',
    name: 'Ravn Galdra',
    persona: 'verifier',
    profile: 'consistency-checks',
    deployment: 'launchd',
    deploymentKwargs: {},
    model: 'claude-sonnet-4-6',
    mountNames: ['shared'],
    writeMount: 'shared',
    readMountNames: ['shared'],
    writeMountNames: ['shared'],
    categoryScope: ['lint', 'wikilinks'],
    features: DEFAULT_WARDEN_FEATURES,
    schedules: DEFAULT_WARDEN_SCHEDULES,
    console: DEFAULT_WARDEN_CONSOLE,
    autostart: false,
    createdAt: '2026-04-15T10:10:00Z',
    createdBy: 'operator',
    runtime: {
      state: 'offline',
      pagesTouched: 0,
      lastDream: null,
    },
    supervisor: defaultSupervisor('ravn-galdra', false, 'offline', 'launchd'),
    operator: {
      rune: 'ᚷ',
      role: 'verify',
      bio: 'Verifies knowledge consistency and resolves broken wikilinks across mounts',
      expertise: ['lint', 'wikilinks'],
      tools: ['mimir', 'lint-fix'],
    },
  },
  {
    id: 'ravn-saga',
    name: 'Ravn Saga',
    persona: 'mimir-curator',
    profile: 'history-indexing',
    deployment: 'launchd',
    deploymentKwargs: {},
    model: 'gpt-5.5',
    mountNames: ['shared', 'forge'],
    writeMount: 'shared',
    readMountNames: ['shared', 'forge'],
    writeMountNames: ['shared'],
    categoryScope: ['sagas', 'runs'],
    features: DEFAULT_WARDEN_FEATURES,
    schedules: DEFAULT_WARDEN_SCHEDULES,
    console: DEFAULT_WARDEN_CONSOLE,
    autostart: false,
    createdAt: '2026-04-15T10:15:00Z',
    createdBy: 'operator',
    runtime: {
      state: 'idle',
      pagesTouched: 34,
      lastStartedAt: '2026-04-18T03:10:00Z',
      lastDream: {
        id: 'dream-002',
        timestamp: '2026-04-18T03:00:00Z',
        ravn: 'ravn-fjolnir',
        mounts: ['shared', 'platform'],
        pagesUpdated: 14,
        entitiesCreated: 5,
        lintFixes: 3,
        durationMs: 67000,
      },
    },
    supervisor: defaultSupervisor('ravn-saga', true, 'idle', 'launchd'),
    operator: {
      rune: 'ᛊ',
      role: 'index',
      bio: 'Indexes saga histories and run outcomes into long-term knowledge',
      expertise: ['sagas', 'runs'],
      tools: ['mimir', 'ting'],
    },
  },
  {
    id: 'ravn-eir',
    name: 'Ravn Eir',
    persona: 'health-auditor',
    profile: 'ops-watch',
    deployment: 'systemd',
    deploymentKwargs: {},
    model: 'claude-sonnet-4-6',
    mountNames: ['local', 'shared'],
    writeMount: 'local',
    readMountNames: ['local', 'shared'],
    writeMountNames: ['local'],
    categoryScope: ['monitoring', 'runbooks'],
    features: DEFAULT_WARDEN_FEATURES,
    schedules: DEFAULT_WARDEN_SCHEDULES,
    console: DEFAULT_WARDEN_CONSOLE,
    autostart: true,
    createdAt: '2026-04-15T10:20:00Z',
    createdBy: 'operator',
    runtime: {
      state: 'active',
      pagesTouched: 19,
      lastStartedAt: '2026-04-19T03:05:00Z',
      lastDream: {
        id: 'dream-001',
        timestamp: '2026-04-19T03:00:00Z',
        ravn: 'ravn-fjolnir',
        mounts: ['local', 'shared'],
        pagesUpdated: 8,
        entitiesCreated: 2,
        lintFixes: 1,
        durationMs: 42000,
      },
    },
    supervisor: defaultSupervisor('ravn-eir', true, 'active', 'systemd'),
    operator: {
      rune: 'ᛖ',
      role: 'verify',
      bio: 'Monitors deployment health and synthesises runbook updates',
      expertise: ['monitoring', 'runbooks'],
      tools: ['mimir', 'web', 'lint-fix'],
    },
  },
  {
    id: 'ravn-vor',
    name: 'Ravn Vor',
    persona: 'planning-agent',
    profile: 'adr-compliance',
    deployment: 'k8s-gitops',
    deploymentKwargs: {
      repo_path: '/Users/jozefvaneenbergen/gitops/platform',
      namespace: 'ravn-dev',
      manifests_subdir: 'clusters/dev/wardens',
      auto_commit: true,
    },
    model: 'gpt-5.5',
    mountNames: ['platform'],
    writeMount: 'platform',
    readMountNames: ['platform'],
    writeMountNames: ['platform'],
    categoryScope: ['adr', 'compliance'],
    features: DEFAULT_WARDEN_FEATURES,
    schedules: DEFAULT_WARDEN_SCHEDULES,
    console: DEFAULT_WARDEN_CONSOLE,
    autostart: false,
    createdAt: '2026-04-15T10:25:00Z',
    createdBy: 'operator',
    runtime: {
      state: 'idle',
      pagesTouched: 11,
      lastDream: null,
    },
    supervisor: defaultSupervisor('ravn-vor', true, 'idle', 'k8s-gitops'),
    operator: {
      rune: 'ᚹ',
      role: 'build',
      bio: 'Compiles and cross-references ADR documents with implementation state',
      expertise: ['adr', 'compliance'],
      tools: ['mimir', 'file'],
    },
  },
];

const SEED_RAVENS: Ravn[] = [
  {
    id: 'a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c',
    personaName: 'sindri',
    status: 'active',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:00:00Z',
    location: 'valaskjalf',
    deployment: 'production',
    role: 'build',
    letter: 'S',
    summary: 'Writes and edits source code across the stack.',
    iterationBudget: 40,
    writeRouting: 'local',
    cascade: 'sequential',
    mounts: [
      { name: 'codebase', role: 'primary' },
      { name: 'docs', role: 'ro' },
    ],
    mcpServers: ['filesystem', 'git', 'bash'],
    gatewayChannels: ['slack-dev', 'github-webhook'],
    eventSubscriptions: ['code.requested', 'bug.fix.requested', 'code.changed'],
    residentName: 'sindri',
    peerId: 'peer-sindri-01',
    kind: 'resident',
    chatEndpoint: null,
    sessionId: '0f8e7d6c-5b4a-4392-8170-6e5d4c3b2a19',
  },
  {
    id: 'b7e2c9d1-3a4f-4b8e-a1c6-5d7f8e9a0b2c',
    personaName: 'víðar',
    status: 'active',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:00:00Z',
    location: 'valhalla',
    deployment: 'production',
    role: 'autonomy',
    letter: 'V',
    summary: 'Fully autonomous general-purpose agent.',
    iterationBudget: 100,
    writeRouting: 'shared',
    cascade: 'parallel',
    mounts: [
      { name: 'codebase', role: 'ro' },
      { name: 'reviews', role: 'primary' },
    ],
    mcpServers: ['filesystem', 'git'],
    gatewayChannels: ['github-pr'],
    eventSubscriptions: ['code.changed', 'review.requested', 'review.completed'],
  },
  {
    id: 'c4d5e6f7-1a2b-4c3d-8e9f-0a1b2c3d4e5f',
    personaName: 'muninn',
    status: 'active',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:00:00Z',
    location: 'valaskjalf',
    deployment: 'production',
    role: 'knowledge',
    letter: 'M',
    summary: 'Curates and indexes knowledge into Mimir.',
    iterationBudget: 20,
    writeRouting: 'domain',
    cascade: 'sequential',
    mounts: [{ name: 'codebase', role: 'ro' }],
    mcpServers: ['filesystem', 'mimir'],
    gatewayChannels: [],
    eventSubscriptions: ['code.changed', 'mimir.index.requested'],
  },
  {
    id: 'd8e9f0a1-2b3c-4d5e-6f7a-8b9c0d1e2f3a',
    personaName: 'gefjon',
    status: 'active',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:00:00Z',
    location: 'gimle',
    deployment: 'production',
    role: 'review',
    letter: 'G',
    summary: 'Periodic deep security audits.',
    iterationBudget: 30,
    writeRouting: 'local',
    cascade: 'sequential',
    mounts: [
      { name: 'codebase', role: 'ro' },
      { name: 'audit-results', role: 'primary' },
    ],
    mcpServers: ['filesystem', 'bash', 'sast'],
    gatewayChannels: ['slack-security'],
    eventSubscriptions: ['code.changed', 'security.audit.requested'],
  },
  {
    id: 'e1f2a3b4-5c6d-4e7f-8a9b-0c1d2e3f4a5b',
    personaName: 'fjölnir',
    status: 'active',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:00:00Z',
    location: 'valhalla',
    deployment: 'production',
    role: 'review',
    letter: 'F',
    summary: 'Reviews code changes and provides feedback.',
    iterationBudget: 25,
    writeRouting: 'local',
    cascade: 'sequential',
    mounts: [{ name: 'codebase', role: 'ro' }],
    mcpServers: ['filesystem', 'git'],
    gatewayChannels: ['github-pr'],
    eventSubscriptions: ['code.changed', 'review.requested'],
  },
  {
    id: 'f5a6b7c8-9d0e-4f1a-2b3c-4d5e6f7a8b9c',
    personaName: 'vör',
    status: 'idle',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:00:00Z',
    location: 'gimle',
    deployment: 'production',
    role: 'plan',
    letter: 'V',
    summary: 'Decomposes goals into actionable plans.',
    iterationBudget: 25,
    writeRouting: 'shared',
    cascade: 'parallel',
    mounts: [{ name: 'plans', role: 'primary' }],
    mcpServers: ['filesystem', 'mimir'],
    gatewayChannels: ['slack-planning'],
    eventSubscriptions: ['plan.requested', 'feature.requested'],
  },
  {
    id: '11111111-1111-4111-a111-111111111111',
    personaName: 'saga',
    status: 'active',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:00:00Z',
    location: 'valaskjalf',
    deployment: 'production',
    role: 'report',
    letter: 'S',
    summary: 'Produces daily and weekly recap reports.',
    iterationBudget: 15,
    writeRouting: 'shared',
    cascade: 'sequential',
    mounts: [{ name: 'reports', role: 'primary' }],
    mcpServers: ['filesystem', 'mimir'],
    gatewayChannels: ['slack-reports'],
    eventSubscriptions: ['recap.requested', 'cron.daily'],
  },
  {
    id: '22222222-2222-4222-a222-222222222222',
    personaName: 'eir',
    status: 'active',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:00:00Z',
    location: 'gimle',
    deployment: 'production',
    role: 'observe',
    letter: 'E',
    summary: 'Periodically audits system health metrics.',
    iterationBudget: 20,
    writeRouting: 'shared',
    cascade: 'parallel',
    mounts: [{ name: 'metrics', role: 'ro' }],
    mcpServers: ['metrics-query', 'log-query'],
    gatewayChannels: ['slack-ops'],
    eventSubscriptions: ['health.check.requested', 'cron.hourly'],
  },
  {
    id: '33333333-3333-4333-a333-333333333333',
    personaName: 'bragi',
    status: 'active',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:00:00Z',
    location: 'valhalla',
    deployment: 'production',
    role: 'coord',
    letter: 'B',
    summary: 'Orchestrates multi-step workflows.',
    iterationBudget: 30,
    writeRouting: 'shared',
    cascade: 'parallel',
    mounts: [{ name: 'codebase', role: 'ro' }],
    mcpServers: ['filesystem', 'ravn'],
    gatewayChannels: ['slack-dev'],
    eventSubscriptions: ['workflow.requested', 'cascade.triggered'],
  },
  {
    id: '44444444-4444-4444-a444-444444444444',
    personaName: 'delling',
    status: 'active',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:00:00Z',
    location: 'valaskjalf',
    deployment: 'production',
    role: 'build',
    letter: 'D',
    summary: 'End-to-end coding agent with Mimir access.',
    iterationBudget: 40,
    writeRouting: 'local',
    cascade: 'sequential',
    mounts: [
      { name: 'codebase', role: 'primary' },
      { name: 'docs', role: 'ro' },
    ],
    mcpServers: ['filesystem', 'git', 'bash'],
    gatewayChannels: ['slack-dev'],
    eventSubscriptions: ['code.requested', 'build.requested'],
  },
  {
    id: '55555555-5555-4555-a555-555555555555',
    personaName: 'nótt',
    status: 'idle',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:00:00Z',
    location: 'valhalla',
    deployment: 'production',
    role: 'investigate',
    letter: 'N',
    summary: 'Root-cause analysis for incidents and bugs.',
    iterationBudget: 40,
    writeRouting: 'local',
    cascade: 'sequential',
    mounts: [{ name: 'codebase', role: 'ro' }],
    mcpServers: ['filesystem', 'git', 'bash'],
    gatewayChannels: ['pagerduty', 'slack-incidents'],
    eventSubscriptions: ['bug.reported', 'incident.opened'],
  },
  {
    id: '66666666-6666-4666-a666-666666666666',
    personaName: 'höðr',
    status: 'active',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:00:00Z',
    location: 'gimle',
    deployment: 'staging',
    role: 'qa',
    letter: 'H',
    summary: 'Runs test suites and validates code quality.',
    iterationBudget: 30,
    writeRouting: 'local',
    cascade: 'sequential',
    mounts: [
      { name: 'codebase', role: 'ro' },
      { name: 'test-results', role: 'primary' },
    ],
    mcpServers: ['filesystem', 'bash', 'test-runner'],
    gatewayChannels: ['slack-qa'],
    eventSubscriptions: ['review.completed', 'test.requested'],
  },
];

const SEED_SESSIONS: Session[] = [
  {
    id: '10000001-0000-4000-8000-000000000001',
    ravnId: 'a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c',
    personaName: 'sindri',
    personaRole: 'build',
    personaLetter: 'S',
    status: 'running',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:30:00Z',
    title: 'Implement login form',
    messageCount: 6,
    tokenCount: 4820,
    costUsd: 0.18,
    // Mock never opens sockets — a null endpoint keeps the read-only surface.
    chatEndpoint: null,
  },
  {
    id: '10000001-0000-4000-8000-000000000002',
    ravnId: 'b7e2c9d1-3a4f-4b8e-a1c6-5d7f8e9a0b2c',
    personaName: 'víðar',
    personaRole: 'autonomy',
    personaLetter: 'V',
    status: 'running',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:15:00Z',
    title: 'Autonomous refactor — auth module',
    messageCount: 4,
    tokenCount: 11200,
    costUsd: 0.42,
  },
  {
    id: '10000001-0000-4000-8000-000000000003',
    ravnId: 'c4d5e6f7-1a2b-4c3d-8e9f-0a1b2c3d4e5f',
    personaName: 'muninn',
    personaRole: 'knowledge',
    personaLetter: 'M',
    status: 'running',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:10:00Z',
    title: 'Index new knowledge docs',
    messageCount: 12,
    tokenCount: 1890,
    costUsd: 0.07,
  },
  {
    id: '10000001-0000-4000-8000-000000000004',
    ravnId: 'd8e9f0a1-2b3c-4d5e-6f7a-8b9c0d1e2f3a',
    personaName: 'gefjon',
    personaRole: 'review',
    personaLetter: 'G',
    status: 'running',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:05:00Z',
    title: 'Security audit — API endpoints',
    messageCount: 8,
    tokenCount: 8300,
    costUsd: 0.31,
  },
  {
    id: '10000001-0000-4000-8000-000000000005',
    ravnId: 'e1f2a3b4-5c6d-4e7f-8a9b-0c1d2e3f4a5b',
    personaName: 'fjölnir',
    personaRole: 'review',
    personaLetter: 'F',
    status: 'running',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:00:00Z',
    title: 'Review PR #142',
    messageCount: 23,
    tokenCount: 30100,
    costUsd: 1.14,
  },
  {
    id: '10000001-0000-4000-8000-000000000006',
    ravnId: 'f5a6b7c8-9d0e-4f1a-2b3c-4d5e6f7a8b9c',
    personaName: 'vör',
    personaRole: 'plan',
    personaLetter: 'V',
    status: 'idle',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:00:00Z',
    title: 'Sprint planning — Q2',
    messageCount: 5,
    tokenCount: 2410,
    costUsd: 0.09,
  },
  {
    id: '10000001-0000-4000-8000-000000000007',
    ravnId: '11111111-1111-4111-a111-111111111111',
    personaName: 'saga',
    personaRole: 'report',
    personaLetter: 'S',
    status: 'running',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:20:00Z',
    title: 'Weekly recap — Jan 15',
    messageCount: 3,
    tokenCount: 1200,
    costUsd: 0.02,
  },
  {
    id: '10000001-0000-4000-8000-000000000008',
    ravnId: '22222222-2222-4222-a222-222222222222',
    personaName: 'eir',
    personaRole: 'observe',
    personaLetter: 'E',
    status: 'running',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:25:00Z',
    title: 'Health check — cluster nodes',
    messageCount: 2,
    tokenCount: 800,
    costUsd: 0.04,
  },
  {
    id: '10000001-0000-4000-8000-000000000009',
    ravnId: '33333333-3333-4333-a333-333333333333',
    personaName: 'bragi',
    personaRole: 'coord',
    personaLetter: 'B',
    status: 'running',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:35:00Z',
    title: 'Coordinate deploy pipeline',
    messageCount: 4,
    tokenCount: 1600,
    costUsd: 0.06,
  },
  {
    id: '10000001-0000-4000-8000-000000000010',
    ravnId: '44444444-4444-4444-a444-444444444444',
    personaName: 'delling',
    personaRole: 'build',
    personaLetter: 'D',
    status: 'running',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:40:00Z',
    title: 'Build dashboard widgets',
    messageCount: 5,
    tokenCount: 2100,
    costUsd: 0.08,
  },
  {
    id: '10000001-0000-4000-8000-000000000011',
    ravnId: '55555555-5555-4555-a555-555555555555',
    personaName: 'nótt',
    personaRole: 'investigate',
    personaLetter: 'N',
    status: 'stopped',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:00:00Z',
    title: 'Monitor overnight alerts',
    messageCount: 1,
    tokenCount: 400,
    costUsd: 0.01,
  },
  {
    id: '10000001-0000-4000-8000-000000000012',
    ravnId: '66666666-6666-4666-a666-666666666666',
    personaName: 'höðr',
    personaRole: 'qa',
    personaLetter: 'H',
    status: 'running',
    model: 'claude-4-sonnet',
    createdAt: '2026-01-15T08:45:00Z',
    title: 'Run integration tests',
    messageCount: 3,
    tokenCount: 900,
    costUsd: 0.03,
  },
];

const SEED_TRIGGERS: Trigger[] = [
  {
    id: 'aa000001-0000-4000-8000-000000000001',
    kind: 'cron',
    personaName: 'eir',
    spec: '0 * * * *',
    enabled: true,
    createdAt: '2026-01-01T00:00:00Z',
    lastFiredAt: '2026-01-15T08:24:12Z',
    fireCount: 336,
  },
  {
    id: 'aa000001-0000-4000-8000-000000000002',
    kind: 'event',
    personaName: 'fjölnir',
    spec: 'code.changed',
    enabled: true,
    createdAt: '2026-01-01T00:00:00Z',
    lastFiredAt: '2026-01-15T08:25:50Z',
    fireCount: 47,
  },
  {
    id: 'aa000001-0000-4000-8000-000000000003',
    kind: 'event',
    personaName: 'höðr',
    spec: 'review.completed',
    enabled: true,
    createdAt: '2026-01-01T00:00:00Z',
    lastFiredAt: '2026-01-15T08:27:03Z',
    fireCount: 31,
  },
  {
    id: 'aa000001-0000-4000-8000-000000000004',
    kind: 'webhook',
    personaName: 'sindri',
    spec: '/hooks/dispatch',
    enabled: false,
    createdAt: '2026-01-10T12:00:00Z',
    lastFiredAt: '2026-01-15T08:18:44Z',
    fireCount: 3,
  },
  {
    id: 'aa000001-0000-4000-8000-000000000005',
    kind: 'manual',
    personaName: 'nótt',
    spec: 'investigate-incident',
    enabled: true,
    createdAt: '2026-01-12T09:00:00Z',
    lastFiredAt: '2026-01-15T08:23:31Z',
    fireCount: 8,
  },
  {
    id: 'aa000001-0000-4000-8000-000000000006',
    kind: 'webhook',
    personaName: 'sindri',
    spec: '/hooks/dispatch',
    enabled: true,
    createdAt: '2026-01-10T12:00:00Z',
    lastFiredAt: '2026-01-15T08:26:48Z',
    fireCount: 5,
  },
  {
    id: 'aa000001-0000-4000-8000-000000000007',
    kind: 'event',
    personaName: 'muninn',
    spec: 'mimir.index.requested',
    enabled: true,
    createdAt: '2026-01-04T12:00:00Z',
    lastFiredAt: '2026-01-15T08:26:31Z',
    fireCount: 28,
  },
  {
    id: 'aa000001-0000-4000-8000-000000000008',
    kind: 'event',
    personaName: 'gefjon',
    spec: 'security.audit.requested',
    enabled: true,
    createdAt: '2026-01-05T12:00:00Z',
    lastFiredAt: '2026-01-15T08:24:58Z',
    fireCount: 19,
  },
  {
    id: 'aa000001-0000-4000-8000-000000000009',
    kind: 'manual',
    personaName: 'víðar',
    spec: 'drain-incident-queue',
    enabled: true,
    createdAt: '2026-01-06T12:00:00Z',
    lastFiredAt: '2026-01-15T08:27:12Z',
    fireCount: 11,
  },
  {
    id: 'aa000001-0000-4000-8000-000000000010',
    kind: 'event',
    personaName: 'saga',
    spec: 'recap.requested',
    enabled: true,
    createdAt: '2026-01-08T12:00:00Z',
    lastFiredAt: '2026-01-15T08:25:12Z',
    fireCount: 42,
  },
];

const SEED_MESSAGES: Message[] = [
  {
    id: '00000001-0000-4000-8000-000000000001',
    sessionId: '10000001-0000-4000-8000-000000000001',
    kind: 'user',
    content: 'Please implement the login form',
    ts: '2026-01-15T08:30:01Z',
  },
  {
    id: '00000001-0000-4000-8000-000000000002',
    sessionId: '10000001-0000-4000-8000-000000000001',
    kind: 'think',
    content: 'I need to check the existing auth setup first.',
    ts: '2026-01-15T08:30:02Z',
  },
  {
    id: '00000001-0000-4000-8000-000000000003',
    sessionId: '10000001-0000-4000-8000-000000000001',
    kind: 'tool_call',
    content: '{"path": "src/auth/LoginForm.tsx"}',
    ts: '2026-01-15T08:30:03Z',
    toolName: 'file.read',
  },
  {
    id: '00000001-0000-4000-8000-000000000004',
    sessionId: '10000001-0000-4000-8000-000000000001',
    kind: 'tool_result',
    content: '{"content": "// file not found"}',
    ts: '2026-01-15T08:30:04Z',
    toolName: 'file.read',
  },
  {
    id: '00000001-0000-4000-8000-000000000005',
    sessionId: '10000001-0000-4000-8000-000000000001',
    kind: 'asst',
    content: "I'll create the login form at `src/auth/LoginForm.tsx`.",
    ts: '2026-01-15T08:30:06Z',
  },
  {
    id: '00000001-0000-4000-8000-000000000006',
    sessionId: '10000001-0000-4000-8000-000000000001',
    kind: 'emit',
    content: '{"event":"code.changed","payload":{"file":"src/auth/LoginForm.tsx"}}',
    ts: '2026-01-15T08:30:30Z',
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function toDetail(summary: PersonaSummary, req?: PersonaCreateRequest): PersonaDetail {
  const isReviewer = summary.name === 'reviewer';
  return {
    ...summary,
    description:
      req?.description ??
      (isReviewer
        ? 'Careful code reviewer. Blocks on clarity, correctness, tests, and risky tool or permission use.'
        : `${summary.summary} Configured as a ${summary.role} persona with ${summary.permissionMode} permissions.`),
    systemPromptTemplate:
      req?.systemPromptTemplate ??
      (isReviewer
        ? [
            '# reviewer',
            'You are {{name}}, a {{role}} persona.',
            'Review code changes for correctness, clarity, tests, and operational risk.',
            'Block when the diff introduces regressions, missing tests, unsafe permissions, or unbounded side effects.',
            'Emit {{produces.event}} only after a complete pass.',
          ].join('\n')
        : `# ${summary.name}\nYou are the ${summary.name} persona.`),
    forbiddenTools: req?.forbiddenTools ?? (isReviewer ? ['write', 'bash', 'apply_patch'] : []),
    llm: {
      thinkingEnabled: req?.llmThinkingEnabled ?? isReviewer,
      maxTokens: req?.llmMaxTokens ?? 8192,
      temperature: req?.llmTemperature,
    },
    produces: {
      eventType: req?.producesEventType ?? summary.producesEvent,
      schemaDef:
        req?.producesSchema ??
        (isReviewer
          ? {
              verdict: 'string',
              confidence: 'number',
              findings: 'array',
            }
          : {}),
    },
    consumes: {
      events:
        req?.consumesEvents ??
        (isReviewer
          ? [
              { name: 'code.changed', injects: ['diff', 'commit_summary'], trust: 0.8 },
              { name: 'review.requested', injects: ['scope', 'owner'], trust: 0.9 },
            ]
          : summary.consumesEvents.map((name) => ({ name }))),
      schemaDef: req?.consumesSchema ?? (isReviewer ? { diff: 'string', scope: 'string' } : {}),
    },
    fanIn: req?.fanInStrategy
      ? { strategy: req.fanInStrategy, params: req.fanInParams ?? {} }
      : { strategy: 'merge', params: isReviewer ? { mode: 'review.final' } : {} },
    mimirWriteRouting: req?.mimirWriteRouting ?? (isReviewer ? 'local' : undefined),
    yamlSource: '[mock]',
    overrideSource: summary.hasOverride
      ? `volundr/src/ravn/personas/overrides/${summary.name}.yaml`
      : undefined,
  };
}

// ---------------------------------------------------------------------------
// Factory functions
// ---------------------------------------------------------------------------

/** Create a mock IPersonaStore with the seeded built-in personas. */
export function createMockPersonaStore(): IPersonaStore {
  const store = new Map<string, PersonaSummary>(SEED_PERSONAS.map((p) => [p.name, p]));

  return {
    async listPersonas(filter: PersonaFilter = 'all') {
      const all = Array.from(store.values());
      if (filter === 'builtin') return all.filter((p) => p.isBuiltin);
      if (filter === 'custom') return all.filter((p) => !p.isBuiltin);
      return all;
    },

    async getPersona(name: string) {
      const p = store.get(name);
      if (!p) throw new Error(`Persona not found: ${name}`);
      return toDetail(p);
    },

    async getPersonaYaml(name: string) {
      const p = store.get(name);
      if (!p) throw new Error(`Persona not found: ${name}`);
      return [
        `name: ${p.name}`,
        `role: ${p.role}`,
        `letter: ${p.letter}`,
        `color: "${p.color}"`,
        `summary: "${p.summary}"`,
        `permission_mode: ${p.permissionMode}`,
        `iteration_budget: ${p.iterationBudget}`,
        `llm:`,
        `  alias: claude-sonnet-4-6`,
        `  thinking: false`,
        `  max_tokens: 8192`,
        `allowed:`,
        ...p.allowedTools.map((t) => `  - ${t}`),
        `forbidden: []`,
        `produces:`,
        `  event: ${p.producesEvent || 'null'}`,
        `  schema: {}`,
        `consumes:`,
        `  events:`,
        ...p.consumesEvents.map((e) => `    - name: ${e}`),
      ].join('\n');
    },

    async createPersona(req: PersonaCreateRequest) {
      const summary: PersonaSummary = {
        name: req.name,
        role: req.role,
        letter: req.letter,
        color: req.color,
        summary: req.summary,
        permissionMode: req.permissionMode,
        allowedTools: req.allowedTools,
        iterationBudget: req.iterationBudget,
        isBuiltin: false,
        hasOverride: false,
        producesEvent: req.producesEventType,
        consumesEvents: req.consumesEvents.map((e) => e.name),
      };
      store.set(req.name, summary);
      return toDetail(summary, req);
    },

    async updatePersona(name: string, req: PersonaCreateRequest) {
      const existing = store.get(name);
      if (!existing) throw new Error(`Persona not found: ${name}`);
      const updated: PersonaSummary = {
        ...existing,
        role: req.role,
        letter: req.letter,
        color: req.color,
        summary: req.summary,
        permissionMode: req.permissionMode,
        allowedTools: req.allowedTools,
        iterationBudget: req.iterationBudget,
        producesEvent: req.producesEventType,
        consumesEvents: req.consumesEvents.map((e) => e.name),
      };
      store.set(name, updated);
      return toDetail(updated, req);
    },

    async deletePersona(name: string) {
      store.delete(name);
    },

    async forkPersona(name: string, req: PersonaForkRequest) {
      const source = store.get(name);
      if (!source) throw new Error(`Persona not found: ${name}`);
      const forked: PersonaSummary = { ...source, name: req.newName, isBuiltin: false };
      store.set(req.newName, forked);
      return toDetail(forked);
    },
  };
}

/** Create a mock IRavenStream with a seeded fleet. */
export function createMockRavenStream(): IRavenStream {
  return {
    async listRavens() {
      return SEED_RAVENS;
    },

    async getRaven(id: string) {
      const r = SEED_RAVENS.find((rv) => rv.id === id);
      if (!r) throw new Error(`Ravn not found: ${id}`);
      return r;
    },
  };
}

/** Create a mock ISessionStream with seeded sessions and messages. */
export function createMockSessionStream(): ISessionStream {
  return {
    async listSessions() {
      return SEED_SESSIONS;
    },

    async getSession(id: string) {
      const s = SEED_SESSIONS.find((ss) => ss.id === id);
      if (!s) throw new Error(`Session not found: ${id}`);
      return s;
    },

    async getMessages(sessionId: string) {
      return SEED_MESSAGES.filter((m) => m.sessionId === sessionId);
    },
  };
}

/** Create a mock ITriggerStore with seeded triggers. */
export function createMockTriggerStore(): ITriggerStore {
  const store = new Map<string, Trigger>(SEED_TRIGGERS.map((t) => [t.id, t]));
  let nextSeq = SEED_TRIGGERS.length + 1;
  function nextTriggerUuid(): string {
    const n = nextSeq++;
    return `aa000001-0000-4000-8000-${String(n).padStart(12, '0')}`;
  }

  return {
    async listTriggers() {
      return Array.from(store.values());
    },

    async createTrigger(t) {
      const trigger: Trigger = {
        ...t,
        id: nextTriggerUuid(),
        createdAt: new Date().toISOString(),
      };
      store.set(trigger.id, trigger);
      return trigger;
    },

    async deleteTrigger(id: string) {
      store.delete(id);
    },
  };
}

/** Create a mock IBudgetStream with fixed demo values. */
export function createMockBudgetStream(): IBudgetStream {
  const perRavn: Record<string, BudgetState> = {
    // sindri (coder) — $3.61 / $5.00
    'a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c': { spentUsd: 3.61, capUsd: 5.0, warnAt: 0.7 },
    // víðar (autonomous-agent) — $2.83 / $4.00
    'b7e2c9d1-3a4f-4b8e-a1c6-5d7f8e9a0b2c': { spentUsd: 2.83, capUsd: 4.0, warnAt: 0.7 },
    // muninn (mimir-curator) — $1.42 / $2.00
    'c4d5e6f7-1a2b-4c3d-8e9f-0a1b2c3d4e5f': { spentUsd: 1.42, capUsd: 2.0, warnAt: 0.7 },
    // gefjon (security-auditor) — $0.91 / $2.00
    'd8e9f0a1-2b3c-4d5e-6f7a-8b9c0d1e2f3a': { spentUsd: 0.91, capUsd: 2.0, warnAt: 0.7 },
    // fjölnir (reviewer) — $0.88 / $2.00
    'e1f2a3b4-5c6d-4e7f-8a9b-0c1d2e3f4a5b': { spentUsd: 0.88, capUsd: 2.0, warnAt: 0.7 },
    // vör (planning-agent, idle) — $0.00 / $0.50
    'f5a6b7c8-9d0e-4f1a-2b3c-4d5e6f7a8b9c': { spentUsd: 0.0, capUsd: 0.5, warnAt: 0.7 },
    // saga (reporter) — $0.02 / $1.00
    '11111111-1111-4111-a111-111111111111': { spentUsd: 0.02, capUsd: 1.0, warnAt: 0.7 },
    // eir (health-auditor) — $0.04 / $0.25
    '22222222-2222-4222-a222-222222222222': { spentUsd: 0.04, capUsd: 0.25, warnAt: 0.7 },
    // bragi (coordinator) — $0.32 / $1.50
    '33333333-3333-4333-a333-333333333333': { spentUsd: 0.32, capUsd: 1.5, warnAt: 0.7 },
    // delling (coder) — $0.21 / $1.00
    '44444444-4444-4444-a444-444444444444': { spentUsd: 0.21, capUsd: 1.0, warnAt: 0.7 },
    // nótt (investigator, idle) — $0.05 / $0.70
    '55555555-5555-4555-a555-555555555555': { spentUsd: 0.05, capUsd: 0.7, warnAt: 0.7 },
    // höðr (qa-agent) — $0.05 / $0.75
    '66666666-6666-4666-a666-666666666666': { spentUsd: 0.05, capUsd: 0.75, warnAt: 0.7 },
  };

  return {
    async getBudget(ravnId: string) {
      return perRavn[ravnId] ?? { spentUsd: 0, capUsd: 5.0, warnAt: 0.7 };
    },

    async getFleetBudget() {
      const total = Object.values(perRavn).reduce(
        (acc, b) => ({
          spentUsd: acc.spentUsd + b.spentUsd,
          capUsd: acc.capUsd + b.capUsd,
          warnAt: 0.7,
        }),
        { spentUsd: 0, capUsd: 0, warnAt: 0.7 },
      );
      return total;
    },
  };
}

function slugifyWardenName(name: string): string {
  const normalized = name.trim().toLowerCase();
  let slug = '';
  let sawSeparator = false;

  for (const char of normalized) {
    const code = char.charCodeAt(0);
    const isLowerAlpha = code >= 97 && code <= 122;
    const isDigit = code >= 48 && code <= 57;

    if (isLowerAlpha || isDigit) {
      if (sawSeparator && slug) slug += '-';
      slug += char;
      sawSeparator = false;
      continue;
    }

    sawSeparator = slug.length > 0;
  }

  return slug;
}

/** Create a mock IWardenStore with seeded wardens. */
export function createMockWardenStore(): IWardenStore {
  let wardens = [...SEED_WARDENS];
  const listeners = new Map<string, Set<(warden: WardenSummary) => void>>();

  function emit(warden: WardenSummary): void {
    const subscribers = listeners.get(warden.id);
    if (!subscribers) return;
    for (const listener of subscribers) listener(warden);
  }

  return {
    async listWardens() {
      return wardens;
    },

    async getWarden(id: string) {
      const warden = wardens.find((entry) => entry.id === id);
      if (!warden) throw new Error(`Warden not found: ${id}`);
      return warden;
    },

    async createWarden(req: WardenCreateRequest) {
      const baseId = slugifyWardenName(req.name) || 'warden';
      const taken = new Set(wardens.map((entry) => entry.id));
      let nextId = baseId;
      let suffix = 2;
      while (taken.has(nextId)) {
        nextId = `${baseId}-${suffix++}`;
      }

      const created: WardenSummary = {
        id: nextId,
        name: req.name,
        persona: req.persona ?? 'mimir-warden',
        profile: req.profile ?? '',
        deployment: req.deployment ?? 'launchd',
        deploymentKwargs: req.deploymentKwargs ?? {},
        model: req.model ?? 'claude-sonnet-4-6',
        mountNames: req.mountNames ?? [],
        writeMount: req.writeMount ?? '',
        readMountNames: req.readMountNames ?? req.mountNames ?? [],
        writeMountNames: req.writeMountNames ?? (req.writeMount ? [req.writeMount] : []),
        categoryScope: req.categoryScope ?? [],
        features: {
          ...DEFAULT_WARDEN_FEATURES,
          ...req.features,
        },
        schedules: {
          ...DEFAULT_WARDEN_SCHEDULES,
          ...req.schedules,
        },
        console: {
          ...DEFAULT_WARDEN_CONSOLE,
          ...req.console,
        },
        autostart: req.autostart ?? false,
        createdAt: new Date().toISOString(),
        createdBy: req.createdBy ?? 'operator',
        runtime: {
          state: 'offline',
          pagesTouched: 0,
          lastDream: null,
        },
        supervisor: defaultSupervisor(nextId, false, 'offline', req.deployment ?? 'launchd'),
      };
      wardens = [...wardens, created];
      emit(created);
      return created;
    },
    subscribeWarden(id: string, listener) {
      const subscribers = listeners.get(id) ?? new Set();
      subscribers.add(listener);
      listeners.set(id, subscribers);
      return () => {
        const current = listeners.get(id);
        if (!current) return;
        current.delete(listener);
        if (current.size === 0) listeners.delete(id);
      };
    },
    async observeWarden(id: string) {
      const current = wardens.find((entry) => entry.id === id);
      if (!current) throw new Error(`Warden not found: ${id}`);
      const observed: WardenSummary = {
        ...current,
        supervisor: {
          installed: Boolean(current.supervisor?.installed),
          serviceLabel: current.supervisor?.serviceLabel,
          serviceFile: current.supervisor?.serviceFile,
          configFile: current.supervisor?.configFile,
          startCommand: current.supervisor?.startCommand,
          lastInstallAt: current.supervisor?.lastInstallAt,
          ...current.supervisor,
          observation: {
            status:
              current.runtime?.state === 'active'
                ? 'running'
                : current.supervisor?.installed
                  ? 'idle'
                  : 'missing',
            detail:
              current.runtime?.state === 'active'
                ? 'Mock backend reports the warden as running'
                : current.supervisor?.installed
                  ? 'Mock backend reports the warden as installed but idle'
                  : 'Mock backend reports no installed deployment',
            source: current.deployment.startsWith('k8s') ? 'mock-kubernetes' : 'mock-supervisor',
            checkedAt: new Date().toISOString(),
            fields: [],
          },
        },
      };
      wardens = wardens.map((entry) => (entry.id === id ? observed : entry));
      emit(observed);
      return observed;
    },
    async installWarden(id: string) {
      const current = wardens.find((entry) => entry.id === id);
      if (!current) throw new Error(`Warden not found: ${id}`);
      const installed: WardenSummary = {
        ...current,
        runtime: {
          ...current.runtime,
          state: current.runtime?.state === 'active' ? 'active' : 'idle',
        },
        supervisor: {
          ...defaultSupervisor(id, true, current.runtime?.state ?? 'idle', current.deployment),
          installed: true,
          lastInstallAt: new Date().toISOString(),
        },
      };
      wardens = wardens.map((entry) => (entry.id === id ? installed : entry));
      emit(installed);
      return installed;
    },
    async startWarden(id: string) {
      const current = wardens.find((entry) => entry.id === id);
      if (!current) throw new Error(`Warden not found: ${id}`);
      if (!current.supervisor?.installed) {
        throw new Error('Warden must be installed before it can be started');
      }
      const started: WardenSummary = {
        ...current,
        runtime: {
          ...current.runtime,
          state: 'active',
          lastStartedAt: new Date().toISOString(),
        },
      };
      wardens = wardens.map((entry) => (entry.id === id ? started : entry));
      emit(started);
      return started;
    },
    async stopWarden(id: string) {
      const current = wardens.find((entry) => entry.id === id);
      if (!current) throw new Error(`Warden not found: ${id}`);
      if (!current.supervisor?.installed) {
        throw new Error('Warden must be installed before it can be stopped');
      }
      const stopped: WardenSummary = {
        ...current,
        runtime: {
          ...current.runtime,
          state: 'idle',
        },
      };
      wardens = wardens.map((entry) => (entry.id === id ? stopped : entry));
      emit(stopped);
      return stopped;
    },
    async uninstallWarden(id: string) {
      const current = wardens.find((entry) => entry.id === id);
      if (!current) throw new Error(`Warden not found: ${id}`);
      const uninstalled: WardenSummary = {
        ...current,
        runtime: {
          ...current.runtime,
          state: 'offline',
        },
        supervisor: defaultSupervisor(id, false, 'offline', current.deployment),
      };
      wardens = wardens.map((entry) => (entry.id === id ? uninstalled : entry));
      emit(uninstalled);
      return uninstalled;
    },
    async getWardenLogs(id, options) {
      const warden = wardens.find((entry) => entry.id === id);
      if (!warden) throw new Error(`Warden not found: ${id}`);
      return [
        {
          id: `${id}-${options?.stream ?? 'stdout'}-1`,
          source: options?.stream ?? 'stdout',
          lineNumber: 1,
          raw: '',
          timestamp: new Date().toISOString(),
          level: 'INFO',
          logger: 'ravn.daemon',
          message: 'ravn daemon started.',
        },
      ];
    },
    async getWardenActivity(id) {
      const warden = wardens.find((entry) => entry.id === id);
      if (!warden) throw new Error(`Warden not found: ${id}`);
      return [
        {
          id: `${id}-activity-1`,
          source: 'stdout',
          lineNumber: 1,
          raw: '',
          timestamp: new Date().toISOString(),
          level: warden.runtime?.state === 'active' ? 'INFO' : 'WARN',
          logger: 'ravn.dream',
          message:
            warden.runtime?.state === 'active'
              ? 'dream cycle scheduled and waiting for next cron window'
              : 'warden is installed but idle',
        },
      ];
    },
  };
}
