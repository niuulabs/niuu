/**
 * Test fixtures for the persona library. Canned values, test-only.
 */
import type { PersonaSummary } from '@niuulabs/domain';
import type { PersonaDetail } from '../ports';

export function makePersonaSummary(overrides: Partial<PersonaSummary> = {}): PersonaSummary {
  return {
    name: 'reviewer',
    role: 'review',
    letter: 'R',
    color: 'var(--color-accent-indigo)',
    summary: 'Reviews code changes and provides feedback.',
    permissionMode: 'default',
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
    llm: { thinkingEnabled: true, maxTokens: 0 },
    produces: { eventType: 'review.completed', schemaDef: { verdict: 'string' }, when: {} },
    consumes: { events: [{ name: 'code.changed' }], schemaDef: {} },
    fanIn: { strategy: 'all_must_pass', params: { contributes_to: 'review.verdict' } },
    yamlSource: '[built-in]',
    ...overrides,
  };
}
