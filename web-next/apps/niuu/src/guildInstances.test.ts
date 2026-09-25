import { describe, expect, it } from 'vitest';
import { normalizeHealth } from './guildInstances';

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
