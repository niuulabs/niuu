import { describe, it, expect } from 'vitest';
import { sessionStatusSchema, sessionSchema } from './session';

// ---------------------------------------------------------------------------
// sessionStatusSchema
// ---------------------------------------------------------------------------

describe('sessionStatusSchema', () => {
  it.each(['running', 'idle', 'stopped', 'failed'])('accepts status "%s"', (s) => {
    expect(sessionStatusSchema.parse(s)).toBe(s);
  });

  it('rejects an unknown status', () => {
    expect(() => sessionStatusSchema.parse('completed')).toThrow();
  });
});

// ---------------------------------------------------------------------------
// sessionSchema
// ---------------------------------------------------------------------------

const validSession = {
  id: 'a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c',
  ravnId: 'b7e2c9d1-3a4f-4b8e-a1c6-5d7f8e9a0b2c',
  personaName: 'sindri',
  status: 'running',
  model: 'claude-sonnet-4-6',
  createdAt: '2026-04-15T09:12:34Z',
} as const;

describe('sessionSchema', () => {
  it('round-trips a valid session', () => {
    const result = sessionSchema.parse(validSession);
    expect(result).toMatchObject(validSession);
  });

  it('rejects invalid UUID for id', () => {
    expect(() => sessionSchema.parse({ ...validSession, id: 'bad-id' })).toThrow();
  });

  it('rejects empty ravnId', () => {
    expect(() => sessionSchema.parse({ ...validSession, ravnId: '' })).toThrow();
  });

  it('rejects empty personaName', () => {
    expect(() => sessionSchema.parse({ ...validSession, personaName: '' })).toThrow();
  });

  it('rejects empty model', () => {
    expect(() => sessionSchema.parse({ ...validSession, model: '' })).toThrow();
  });

  it('rejects invalid status', () => {
    expect(() => sessionSchema.parse({ ...validSession, status: 'active' })).toThrow();
  });

  it('rejects malformed createdAt', () => {
    expect(() => sessionSchema.parse({ ...validSession, createdAt: 'yesterday' })).toThrow();
  });

  it('accepts all valid statuses', () => {
    for (const status of ['running', 'idle', 'stopped', 'failed'] as const) {
      expect(sessionSchema.parse({ ...validSession, status }).status).toBe(status);
    }
  });
});
