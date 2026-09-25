/**
 * Test fixtures for the Ravens workbench and persona library. Canned values,
 * test-only.
 */
import type { PersonaSummary } from '@niuulabs/domain';
import type { Ravn, ResidentDeploymentProfile } from '../domain/ravn';
import type { Session } from '../domain/session';
import type { PersonaDetail } from '../ports';

export const RAVN_ID = '11111111-1111-4111-8111-111111111111';
export const SESSION_ID = '22222222-2222-4222-8222-222222222222';

export function makeRavn(overrides: Partial<Ravn> = {}): Ravn {
  return {
    id: RAVN_ID,
    personaName: 'reviewer',
    residentName: 'Muninn',
    status: 'active',
    model: 'claude-sonnet-4-6',
    createdAt: '2026-09-01T10:00:00Z',
    updatedAt: '2026-09-24T09:00:00Z',
    kind: 'resident',
    managed: true,
    engine: 'ravn',
    backend: 'local',
    profileId: 'ravn-local',
    desiredState: 'running',
    observedState: 'active',
    capabilities: ['chat', 'runtime.restart', 'runtime.suspend', 'logs', 'usage'],
    conditions: [
      {
        type: 'BackendReady',
        status: 'true',
        reason: 'Ready',
        message: 'runtime ready',
        lastTransitionAt: '2026-09-24T09:00:00Z',
      },
    ],
    instanceId: 'target-a',
    instanceName: 'Local Forge',
    messageCount: 12,
    tokenCount: 34_904,
    costUsd: 0.07,
    ...overrides,
  } as Ravn;
}

export function failedRavn(overrides: Partial<Ravn> = {}): Ravn {
  return makeRavn({
    status: 'failed',
    observedState: 'failed',
    conditions: [
      {
        type: 'BackendReady',
        status: 'unknown',
        reason: 'ReconcileFailed',
        message: "('Connection aborted.', FileNotFoundError(2, 'No such file or directory'))",
        lastTransitionAt: '2026-09-24T09:00:00Z',
      },
    ],
    ...overrides,
  });
}

export function makeSession(overrides: Partial<Session> = {}): Session {
  return {
    id: SESSION_ID,
    ravnId: RAVN_ID,
    personaName: 'reviewer',
    status: 'running',
    model: 'claude-sonnet-4-6',
    createdAt: '2026-09-24T08:00:00Z',
    title: 'Morning check',
    messageCount: 4,
    chatEndpoint: 'ws://localhost/s/abc/session',
    instanceId: 'target-a',
    ...overrides,
  } as Session;
}

export function makePersonaSummary(overrides: Partial<PersonaSummary> = {}): PersonaSummary {
  return {
    name: 'reviewer',
    role: 'review',
    letter: 'R',
    color: 'var(--color-accent-indigo)',
    summary: 'Reviews code changes and provides feedback.',
    permissionMode: 'default',
    allowedTools: ['file', 'git'],
    iterationBudget: 25,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'review.completed',
    consumesEvents: ['code.changed'],
    ...overrides,
  };
}

export function makePersonaDetail(overrides: Partial<PersonaDetail> = {}): PersonaDetail {
  return {
    ...makePersonaSummary(),
    description: 'Reviews code changes and provides feedback.',
    systemPromptTemplate: '## Identity\nYou are a code reviewer.',
    forbiddenTools: ['cascade'],
    llm: { thinkingEnabled: true, maxTokens: 0 },
    produces: { eventType: 'review.completed', schemaDef: { verdict: 'string' } },
    consumes: { events: [{ name: 'code.changed' }], schemaDef: {} },
    fanIn: { strategy: 'all_must_pass', params: { contributes_to: 'review.verdict' } },
    yamlSource: '[built-in]',
    ...overrides,
  };
}

export function makeProfile(
  overrides: Partial<ResidentDeploymentProfile> = {},
): ResidentDeploymentProfile {
  return {
    id: 'nemoclaw-local',
    displayName: 'NemoClaw (Local)',
    description: 'NVIDIA OpenClaw resident hosted by the local container engine',
    backend: 'local',
    engine: 'openclaw',
    capabilities: ['chat', 'session.list', 'session.create', 'logs'],
    defaultModel: 'niuu/nvidia/nemotron-3-super',
    allowedModels: ['niuu/nvidia/nemotron-3-super'],
    modelPrefix: 'niuu/',
    labels: [],
    instanceId: 'target-a',
    instanceName: 'Local Forge',
    instanceSlug: 'local',
    ...overrides,
  };
}
