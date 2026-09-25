import { describe, it, expect } from 'vitest';
import { triggerKindSchema, triggerSchema } from './trigger';

// ---------------------------------------------------------------------------
// triggerKindSchema
// ---------------------------------------------------------------------------

describe('triggerKindSchema', () => {
  it.each(['cron', 'event'])('accepts kind "%s"', (k) => {
    expect(triggerKindSchema.parse(k)).toBe(k);
  });

  it('rejects an unknown kind', () => {
    expect(() => triggerKindSchema.parse('timer')).toThrow();
  });

  it.each(['webhook', 'manual'])('rejects unsupported kind "%s"', (k) => {
    // Only cron/event are backed by a resident execution engine — the
    // backend rejects these with 422, so the UI does not offer them either.
    expect(() => triggerKindSchema.parse(k)).toThrow();
  });
});

// ---------------------------------------------------------------------------
// triggerSchema
// ---------------------------------------------------------------------------

const validTrigger = {
  id: 'aa000001-0000-4000-8000-000000000001',
  kind: 'cron',
  personaName: 'eir',
  spec: '0 * * * *',
  repo: '',
  enabled: true,
  createdAt: '2026-04-01T00:00:00Z',
} as const;

describe('triggerSchema', () => {
  it('round-trips a valid trigger', () => {
    const result = triggerSchema.parse(validTrigger);
    expect(result).toMatchObject(validTrigger);
  });

  it('rejects invalid UUID for id', () => {
    expect(() => triggerSchema.parse({ ...validTrigger, id: 'bad' })).toThrow();
  });

  it('rejects empty personaName', () => {
    expect(() => triggerSchema.parse({ ...validTrigger, personaName: '' })).toThrow();
  });

  it('accepts empty spec (manual triggers may have no spec)', () => {
    const result = triggerSchema.parse({ ...validTrigger, spec: '' });
    expect(result.spec).toBe('');
  });

  it('accepts enabled=false', () => {
    const result = triggerSchema.parse({ ...validTrigger, enabled: false });
    expect(result.enabled).toBe(false);
  });

  it('rejects invalid kind', () => {
    expect(() => triggerSchema.parse({ ...validTrigger, kind: 'poll' })).toThrow();
  });

  it('accepts all valid kinds', () => {
    for (const kind of ['cron', 'event'] as const) {
      expect(triggerSchema.parse({ ...validTrigger, kind }).kind).toBe(kind);
    }
  });

  it('rejects malformed createdAt', () => {
    expect(() => triggerSchema.parse({ ...validTrigger, createdAt: 'bad-date' })).toThrow();
  });

  it('defaults repo to empty string when omitted', () => {
    const { repo: _omit, ...withoutRepo } = validTrigger;
    const result = triggerSchema.parse(withoutRepo);
    expect(result.repo).toBe('');
  });

  it('round-trips a non-empty repo for an event trigger', () => {
    const result = triggerSchema.parse({ ...validTrigger, kind: 'event', repo: 'org/repo' });
    expect(result.repo).toBe('org/repo');
  });
});
