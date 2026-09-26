import { describe, it, expect } from 'vitest';
import { escapeStep, type MemoryEscapableState } from './escapeStep';

const EMPTY: MemoryEscapableState = { path: [], focus: null, q: null, asOf: null };

describe('escapeStep', () => {
  it('clears the traced path first when present', () => {
    expect(escapeStep({ path: ['/a', '/b'], focus: '/b', q: 'x', asOf: '2026-01-01' })).toBe(
      'path',
    );
  });

  it('clears focus next when no path is traced', () => {
    expect(escapeStep({ ...EMPTY, focus: '/b', q: 'x', asOf: '2026-01-01' })).toBe('focus');
  });

  it('clears the question next when no path or focus', () => {
    expect(escapeStep({ ...EMPTY, q: 'x', asOf: '2026-01-01' })).toBe('q');
  });

  it('exits replay last', () => {
    expect(escapeStep({ ...EMPTY, asOf: '2026-01-01' })).toBe('asOf');
  });

  it('returns null when nothing to step back out of', () => {
    expect(escapeStep(EMPTY)).toBeNull();
  });
});
