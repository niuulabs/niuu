import { describe, it, expect } from 'vitest';
import { ravnStatusSchema, ravnSchema } from './ravn';

// ---------------------------------------------------------------------------
// ravnStatusSchema
// ---------------------------------------------------------------------------

describe('ravnStatusSchema', () => {
  it.each(['active', 'idle', 'suspended', 'failed', 'completed'])('accepts status "%s"', (s) => {
    expect(ravnStatusSchema.parse(s)).toBe(s);
  });

  it('rejects an unknown status', () => {
    expect(() => ravnStatusSchema.parse('unknown')).toThrow();
  });

  it('rejects empty string', () => {
    expect(() => ravnStatusSchema.parse('')).toThrow();
  });
});

// ---------------------------------------------------------------------------
// ravnSchema
// ---------------------------------------------------------------------------

const validRavn = {
  id: 'a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c',
  personaName: 'sindri',
  status: 'active',
  model: 'claude-sonnet-4-6',
  createdAt: '2026-04-15T09:12:34Z',
} as const;

describe('ravnSchema', () => {
  it('round-trips a valid ravn', () => {
    const result = ravnSchema.parse(validRavn);
    expect(result).toMatchObject(validRavn);
  });

  it('accepts an optional updatedAt', () => {
    const result = ravnSchema.parse({ ...validRavn, updatedAt: '2026-04-16T10:00:00Z' });
    expect(result.updatedAt).toBe('2026-04-16T10:00:00Z');
  });

  it('omits updatedAt when not provided', () => {
    const result = ravnSchema.parse(validRavn);
    expect(result.updatedAt).toBeUndefined();
  });

  it('rejects an invalid UUID for id', () => {
    expect(() => ravnSchema.parse({ ...validRavn, id: 'not-a-uuid' })).toThrow();
  });

  it('rejects empty personaName', () => {
    expect(() => ravnSchema.parse({ ...validRavn, personaName: '' })).toThrow();
  });

  it('rejects empty model', () => {
    expect(() => ravnSchema.parse({ ...validRavn, model: '' })).toThrow();
  });

  it('rejects an invalid status', () => {
    expect(() => ravnSchema.parse({ ...validRavn, status: 'running' })).toThrow();
  });

  it('accepts all valid statuses', () => {
    for (const status of ['active', 'idle', 'suspended', 'failed', 'completed'] as const) {
      expect(ravnSchema.parse({ ...validRavn, status }).status).toBe(status);
    }
  });

  it('rejects a malformed createdAt', () => {
    expect(() => ravnSchema.parse({ ...validRavn, createdAt: 'not-a-date' })).toThrow();
  });
});
