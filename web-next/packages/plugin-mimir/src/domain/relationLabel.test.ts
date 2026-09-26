import { describe, it, expect } from 'vitest';
import { relationLabel } from './relationLabel';

describe('relationLabel', () => {
  it('humanises a known typed relation', () => {
    expect(relationLabel('depends_on')).toBe('depends on');
    expect(relationLabel('routes_for')).toBe('routes for');
  });
  it('falls back to underscore-replacement for an unknown typed relation', () => {
    expect(relationLabel('mentions_in')).toBe('mentions in');
  });
  it('returns null for an untyped (plain wikilink) relation', () => {
    expect(relationLabel(null)).toBeNull();
  });
});
