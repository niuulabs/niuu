import { describe, expect, it } from 'vitest';
import { isValidTlsFingerprint, normalizeHealth } from './guildInstances';

describe('normalizeHealth', () => {
  it('passes through every known health value unchanged', () => {
    expect(normalizeHealth('ok')).toBe('ok');
    expect(normalizeHealth('unreachable')).toBe('unreachable');
    expect(normalizeHealth('unknown')).toBe('unknown');
  });

  it('falls back to unknown for a value this build does not recognize', () => {
    // A future backend enum value, or a typo — must never slip through an
    // === 'ok' / === 'unreachable' check and render as either.
    expect(normalizeHealth('degraded')).toBe('unknown');
    expect(normalizeHealth('OK')).toBe('unknown');
  });

  it('falls back to unknown for a missing field from an older backend or fixture', () => {
    expect(normalizeHealth(undefined)).toBe('unknown');
    expect(normalizeHealth(null)).toBe('unknown');
  });
});

describe('isValidTlsFingerprint', () => {
  const validHex = 'ab'.repeat(32);

  it('accepts an empty value — the field is optional', () => {
    expect(isValidTlsFingerprint('')).toBe(true);
    expect(isValidTlsFingerprint('   ')).toBe(true);
  });

  it('accepts a bare 64-character hex digest, any case', () => {
    expect(isValidTlsFingerprint(validHex)).toBe(true);
    expect(isValidTlsFingerprint(validHex.toUpperCase())).toBe(true);
  });

  it('accepts a colon-separated digest', () => {
    const colonized = Array.from({ length: 32 }, () => 'ab').join(':');
    expect(isValidTlsFingerprint(colonized)).toBe(true);
  });

  it('rejects the wrong length', () => {
    expect(isValidTlsFingerprint('ab'.repeat(10))).toBe(false);
  });

  it('rejects non-hex characters', () => {
    expect(isValidTlsFingerprint('zz'.repeat(32))).toBe(false);
  });
});
