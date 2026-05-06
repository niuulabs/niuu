import { describe, it, expect } from 'vitest';
import { activityLogKindSchema, activityLogEntrySchema } from './activityLog';

describe('activityLogKindSchema', () => {
  it('accepts "session"', () => {
    expect(activityLogKindSchema.parse('session')).toBe('session');
  });

  it('accepts "trigger"', () => {
    expect(activityLogKindSchema.parse('trigger')).toBe('trigger');
  });

  it('accepts "emit"', () => {
    expect(activityLogKindSchema.parse('emit')).toBe('emit');
  });

  it('rejects unknown kind', () => {
    expect(() => activityLogKindSchema.parse('unknown')).toThrow();
  });

  it('rejects empty string', () => {
    expect(() => activityLogKindSchema.parse('')).toThrow();
  });
});

describe('activityLogEntrySchema', () => {
  const valid = {
    id: 'session-abc123',
    ts: '2026-04-15T09:12:34Z',
    kind: 'session' as const,
    ravnId: 'a3f1b2c4',
    message: 'Implement login form',
  };

  it('round-trips a valid entry', () => {
    expect(activityLogEntrySchema.parse(valid)).toEqual(valid);
  });

  it('accepts "trigger" kind', () => {
    expect(activityLogEntrySchema.parse({ ...valid, kind: 'trigger' }).kind).toBe('trigger');
  });

  it('accepts "emit" kind', () => {
    expect(activityLogEntrySchema.parse({ ...valid, kind: 'emit' }).kind).toBe('emit');
  });

  it('rejects missing id', () => {
    const { id: _id, ...rest } = valid;
    expect(() => activityLogEntrySchema.parse(rest)).toThrow();
  });

  it('rejects missing ts', () => {
    const { ts: _ts, ...rest } = valid;
    expect(() => activityLogEntrySchema.parse(rest)).toThrow();
  });

  it('rejects unknown kind', () => {
    expect(() => activityLogEntrySchema.parse({ ...valid, kind: 'bad' })).toThrow();
  });
});
