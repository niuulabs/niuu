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

  // ── Resident fields ────────────────────────────────────────────────────────

  it('accepts a resident ravn with all resident fields', () => {
    const result = ravnSchema.parse({
      ...validRavn,
      residentName: 'huginn',
      peerId: 'peer-huginn-01',
      kind: 'resident',
      chatEndpoint: 'wss://skuld.example/s/abc/session',
      sessionId: '0f8e7d6c-5b4a-4392-8170-6e5d4c3b2a19',
    });
    expect(result.residentName).toBe('huginn');
    expect(result.peerId).toBe('peer-huginn-01');
    expect(result.kind).toBe('resident');
    expect(result.chatEndpoint).toBe('wss://skuld.example/s/abc/session');
    expect(result.sessionId).toBe('0f8e7d6c-5b4a-4392-8170-6e5d4c3b2a19');
  });

  it('accepts a record without any resident fields', () => {
    const result = ravnSchema.parse(validRavn);
    expect(result.residentName).toBeUndefined();
    expect(result.peerId).toBeUndefined();
    expect(result.kind).toBeUndefined();
    expect(result.chatEndpoint).toBeUndefined();
    expect(result.sessionId).toBeUndefined();
  });

  it('accepts a null chatEndpoint', () => {
    const result = ravnSchema.parse({ ...validRavn, kind: 'resident', chatEndpoint: null });
    expect(result.chatEndpoint).toBeNull();
  });

  it('accepts kind "persona"', () => {
    expect(ravnSchema.parse({ ...validRavn, kind: 'persona' }).kind).toBe('persona');
  });

  it('rejects an unknown kind', () => {
    expect(() => ravnSchema.parse({ ...validRavn, kind: 'ghost' })).toThrow();
  });
});
