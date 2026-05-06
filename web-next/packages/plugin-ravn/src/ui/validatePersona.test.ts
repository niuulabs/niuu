import { describe, it, expect } from 'vitest';
import { validatePersona } from './validatePersona';
import type { PersonaCreateRequest } from '../ports';
import type { EventCatalog } from '@niuulabs/domain';

const CATALOG: EventCatalog = [
  { name: 'code.changed', schema: { file: 'string' } },
  { name: 'review.completed', schema: { outcome: 'string' } },
  { name: 'qa.completed', schema: {} },
];

function makeReq(overrides: Partial<PersonaCreateRequest> = {}): PersonaCreateRequest {
  return {
    name: 'my-persona',
    role: 'build',
    letter: 'M',
    color: 'var(--color-accent-indigo)',
    summary: 'Test persona',
    description: 'A test persona for unit tests',
    systemPromptTemplate: '# my-persona',
    allowedTools: ['read', 'write'],
    forbiddenTools: [],
    permissionMode: 'default',
    iterationBudget: 20,
    llmPrimaryAlias: 'claude-sonnet-4-6',
    llmThinkingEnabled: false,
    llmMaxTokens: 8192,
    producesEventType: 'code.changed',
    producesSchema: { file: 'string' },
    consumesEvents: [{ name: 'review.completed' }],
    consumesSchema: {},
    ...overrides,
  };
}

describe('validatePersona', () => {
  describe('name validation', () => {
    it('returns no error for a valid name', () => {
      const errors = validatePersona(makeReq(), CATALOG);
      expect(errors.some((e) => e.field === 'name')).toBe(false);
    });

    it('returns an error when name is empty', () => {
      const errors = validatePersona(makeReq({ name: '' }), CATALOG);
      expect(errors.some((e) => e.field === 'name')).toBe(true);
    });

    it('returns an error when name is only whitespace', () => {
      const errors = validatePersona(makeReq({ name: '   ' }), CATALOG);
      expect(errors.some((e) => e.field === 'name')).toBe(true);
    });
  });

  describe('role validation', () => {
    it('returns no error for a valid role', () => {
      const errors = validatePersona(makeReq({ role: 'review' }), CATALOG);
      expect(errors.some((e) => e.field === 'role')).toBe(false);
    });

    it('returns an error for an invalid role', () => {
      const errors = validatePersona(makeReq({ role: 'unknown-role' as never }), CATALOG);
      expect(errors.some((e) => e.field === 'role')).toBe(true);
      expect(errors.find((e) => e.field === 'role')?.message).toContain('unknown-role');
    });

    it('accepts all canonical roles', () => {
      const roles = [
        'arbiter',
        'audit',
        'autonomy',
        'build',
        'coord',
        'gate',
        'index',
        'investigate',
        'knowledge',
        'observe',
        'plan',
        'qa',
        'report',
        'review',
        'ship',
        'verify',
        'write',
      ] as const;
      for (const role of roles) {
        const errors = validatePersona(makeReq({ role }), CATALOG);
        expect(
          errors.some((e) => e.field === 'role'),
          `role ${role} should be valid`,
        ).toBe(false);
      }
    });
  });

  describe('tool allow/deny disjoint validation', () => {
    it('returns no error when allow and deny are disjoint', () => {
      const errors = validatePersona(
        makeReq({ allowedTools: ['read'], forbiddenTools: ['write'] }),
        CATALOG,
      );
      expect(errors.some((e) => e.field === 'tools')).toBe(false);
    });

    it('returns an error when allow and deny overlap', () => {
      const errors = validatePersona(
        makeReq({ allowedTools: ['read', 'write'], forbiddenTools: ['write', 'bash'] }),
        CATALOG,
      );
      const toolError = errors.find((e) => e.field === 'tools');
      expect(toolError).toBeDefined();
      expect(toolError?.message).toContain('write');
    });

    it('lists all overlapping tools in the message', () => {
      const errors = validatePersona(
        makeReq({
          allowedTools: ['read', 'write', 'bash'],
          forbiddenTools: ['write', 'bash'],
        }),
        CATALOG,
      );
      const msg = errors.find((e) => e.field === 'tools')?.message ?? '';
      expect(msg).toContain('write');
      expect(msg).toContain('bash');
    });
  });

  describe('produces event validation', () => {
    it('returns no error when produces event exists in catalog', () => {
      const errors = validatePersona(makeReq({ producesEventType: 'code.changed' }), CATALOG);
      expect(errors.some((e) => e.field === 'produces.eventType')).toBe(false);
    });

    it('returns an error when produces event is not in catalog', () => {
      const errors = validatePersona(makeReq({ producesEventType: 'nonexistent.event' }), CATALOG);
      const e = errors.find((e) => e.field === 'produces.eventType');
      expect(e).toBeDefined();
      expect(e?.message).toContain('nonexistent.event');
    });

    it('returns no error when produces event is empty string', () => {
      const errors = validatePersona(makeReq({ producesEventType: '' }), CATALOG);
      expect(errors.some((e) => e.field === 'produces.eventType')).toBe(false);
    });
  });

  describe('consumes events validation', () => {
    it('returns no error when all consumed events exist in catalog', () => {
      const errors = validatePersona(
        makeReq({
          consumesEvents: [{ name: 'code.changed' }, { name: 'review.completed' }],
        }),
        CATALOG,
      );
      expect(errors.some((e) => e.field.startsWith('consumes.'))).toBe(false);
    });

    it('returns an error for a consumed event not in catalog', () => {
      const errors = validatePersona(
        makeReq({ consumesEvents: [{ name: 'missing.event' }] }),
        CATALOG,
      );
      expect(errors.some((e) => e.field === 'consumes.missing.event')).toBe(true);
    });

    it('skips validation for empty consumed event names', () => {
      const errors = validatePersona(makeReq({ consumesEvents: [{ name: '' }] }), CATALOG);
      expect(errors.some((e) => e.field.startsWith('consumes.'))).toBe(false);
    });
  });

  describe('overall', () => {
    it('returns empty array for a fully valid request', () => {
      const errors = validatePersona(makeReq(), CATALOG);
      expect(errors).toHaveLength(0);
    });

    it('accumulates multiple errors', () => {
      const errors = validatePersona(
        makeReq({
          name: '',
          role: 'bad-role' as never,
          allowedTools: ['shared'],
          forbiddenTools: ['shared'],
          producesEventType: 'no.such.event',
        }),
        CATALOG,
      );
      expect(errors.length).toBeGreaterThan(1);
    });
  });
});
