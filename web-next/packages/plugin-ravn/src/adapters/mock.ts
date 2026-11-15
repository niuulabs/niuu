/**
 * Mock adapters for all Ravn ports.
 *
 * Seeded from the built-in persona definitions that mirror
 * the backend YAML files in src/personas/builtin/.
 */

import type {
  IPersonaStore,
  PersonaSummary,
  PersonaDetail,
  PersonaDocument,
  PersonaForkRequest,
  PersonaFilter,
} from '../ports';
import {
  documentDescription,
  documentInstructions,
  documentName,
  documentPermission,
  documentYaml,
  outcomesOf,
  parseDocumentYaml,
  startsOn,
} from '../application/personaDocument';

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
    iterationBudget: 30,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: 'verification.completed',
    consumesEvents: ['qa.completed', 'code.changed', 'verification.requested'],
  },
];

/** A seeded persona's document, built from what the seed says about it. */
function seedDocument(summary: PersonaSummary): PersonaDocument {
  const prefix = summary.producesEvent.split('.')[0] || summary.name.replace(/-/g, '_');
  const outcomes = Object.entries(summary.outcomeEvents ?? {});
  const map = outcomes.length
    ? Object.fromEntries(outcomes)
    : summary.producesEvent
      ? { done: summary.producesEvent }
      : {};
  return {
    name: summary.name,
    description: summary.summary,
    permission_mode: MODE_BY_API_NAME[summary.permissionMode] ?? 'workspace_write',
    system_prompt_template: `# ${summary.name}\nYou are the ${summary.name} persona.\n`,
    consumes: { event_types: [...summary.consumesEvents] },
    produces: {
      event_type: summary.producesEvent || `${prefix}.completed`,
      event_type_map: map,
      when: Object.fromEntries(
        Object.keys(map).map((verdict) => [
          verdict,
          `The ${summary.name} work ends in ${verdict}.`,
        ]),
      ),
    },
  };
}

const MODE_BY_API_NAME: Record<string, string> = {
  default: 'workspace_write',
  safe: 'read_only',
  loose: 'full_access',
};

const API_NAME_BY_MODE: Record<string, string> = {
  workspace_write: 'default',
  'workspace-write': 'default',
  read_only: 'safe',
  'read-only': 'safe',
  full_access: 'loose',
  'full-access': 'loose',
};

function summaryOf(doc: PersonaDocument, base?: PersonaSummary): PersonaSummary {
  const outcomes = outcomesOf(doc);
  const name = documentName(doc);
  return {
    name,
    role: base?.role ?? 'build',
    letter: base?.letter ?? (name.charAt(0).toUpperCase() || 'P'),
    color: base?.color ?? 'var(--color-accent-indigo)',
    summary: documentDescription(doc) || base?.summary || name,
    permissionMode: API_NAME_BY_MODE[documentPermission(doc)] ?? 'default',
    iterationBudget: base?.iterationBudget ?? 0,
    isBuiltin: base?.isBuiltin ?? false,
    hasOverride: base ? base.isBuiltin : false,
    producesEvent: String((doc.produces as Record<string, unknown> | undefined)?.event_type ?? ''),
    outcomeEvents: Object.fromEntries(outcomes.map((o) => [o.verdict, o.event])),
    consumesEvents: startsOn(doc),
  };
}

function toDetail(summary: PersonaSummary, doc: PersonaDocument): PersonaDetail {
  return {
    ...summary,
    description: documentDescription(doc) || summary.summary,
    systemPromptTemplate: documentInstructions(doc),
    llm: { thinkingEnabled: false, maxTokens: 0 },
    produces: {
      eventType: summary.producesEvent,
      schemaDef: {},
      when: Object.fromEntries(outcomesOf(doc).map((o) => [o.verdict, o.when])),
    },
    consumes: { events: startsOn(doc).map((name) => ({ name })), schemaDef: {} },
    fanIn: { strategy: 'merge', params: {} },
    yamlSource: '[mock]',
    overrideSource: summary.hasOverride ? `[mock override of ${summary.name}]` : undefined,
  };
}

function parsed(yaml: string): PersonaDocument {
  const result = parseDocumentYaml(yaml);
  if (!result.ok) throw new Error(result.error);
  if (!documentName(result.doc)) throw new Error('A persona needs a name');
  return result.doc;
}

// ---------------------------------------------------------------------------
// Factory functions
// ---------------------------------------------------------------------------

/** Create a mock IPersonaStore with the seeded built-in personas. */
export function createMockPersonaStore(): IPersonaStore {
  const documents = new Map<string, PersonaDocument>(
    SEED_PERSONAS.map((p) => [p.name, seedDocument(p)]),
  );
  const store = new Map<string, PersonaSummary>(
    SEED_PERSONAS.map((p) => [
      p.name,
      {
        ...p,
        outcomeEvents: Object.fromEntries(
          outcomesOf(documents.get(p.name)!).map((o) => [o.verdict, o.event]),
        ),
      },
    ]),
  );

  function find(name: string): { summary: PersonaSummary; doc: PersonaDocument } {
    const summary = store.get(name);
    const doc = documents.get(name);
    if (!summary || !doc) throw new Error(`Persona not found: ${name}`);
    return { summary, doc };
  }

  function save(doc: PersonaDocument, base?: PersonaSummary): PersonaDetail {
    const summary = summaryOf(doc, base);
    store.set(summary.name, summary);
    documents.set(summary.name, structuredClone(doc));
    return toDetail(summary, doc);
  }

  return {
    async listPersonas(filter: PersonaFilter = 'all') {
      const all = Array.from(store.values());
      if (filter === 'builtin') return all.filter((p) => p.isBuiltin);
      if (filter === 'custom') return all.filter((p) => !p.isBuiltin);
      return all;
    },

    async getPersona(name: string) {
      const { summary, doc } = find(name);
      return toDetail(summary, doc);
    },

    async getPersonaYaml(name: string) {
      return documentYaml(find(name).doc);
    },

    async getPersonaDocument(name: string) {
      return structuredClone(find(name).doc);
    },

    async checkPersonaYaml(yaml: string) {
      try {
        parsed(yaml);
        return { valid: true, errors: [] };
      } catch (error) {
        return { valid: false, errors: [(error as Error).message] };
      }
    },

    async createPersonaFromYaml(yaml: string) {
      const doc = parsed(yaml);
      if (store.has(documentName(doc))) {
        throw new Error(`Persona already exists: ${documentName(doc)}`);
      }
      return save(doc);
    },

    async replacePersonaYaml(name: string, yaml: string) {
      const { summary } = find(name);
      const doc = parsed(yaml);
      if (documentName(doc) !== name) {
        throw new Error(`The YAML names ${documentName(doc)}, but this is ${name}`);
      }
      return save(doc, summary);
    },

    async deletePersona(name: string) {
      store.delete(name);
      documents.delete(name);
    },

    async forkPersona(name: string, req: PersonaForkRequest) {
      const { doc } = find(name);
      return save({ ...structuredClone(doc), name: req.newName });
    },
  };
}
