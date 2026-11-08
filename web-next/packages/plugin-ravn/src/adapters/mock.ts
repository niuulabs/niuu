/**
 * Mock adapters for all Ravn ports.
 *
 * Seeded from the built-in persona definitions that mirror
 * the backend YAML files in src/ravn/personas/.
 */

import type {
  IPersonaStore,
  PersonaSummary,
  PersonaDetail,
  PersonaCreateRequest,
  PersonaForkRequest,
  PersonaFilter,
} from '../ports';

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
