import { describe, it, expect } from 'vitest';
import { relationLabel, isContradictionRelation } from './relationLabel';

describe('relationLabel', () => {
  it('humanises known typed relations', () => {
    expect(relationLabel('depends_on')).toBe('depends on');
    expect(relationLabel('part_of')).toBe('part of');
    expect(relationLabel('explains')).toBe('explains');
    expect(relationLabel('fixed_by')).toBe('fixed by');
    expect(relationLabel('routes_for')).toBe('routes for');
  });

  it('humanises contradiction relations', () => {
    expect(relationLabel('contradicts')).toBe('contradicts');
    expect(relationLabel('disagrees_with')).toBe('disagrees with');
    expect(relationLabel('conflicts_with')).toBe('conflicts with');
  });

  it('returns null for untyped/structural relations', () => {
    expect(relationLabel('wikilink')).toBeNull();
    expect(relationLabel('link')).toBeNull();
    expect(relationLabel('shared_source')).toBeNull();
    expect(relationLabel('related_entity')).toBeNull();
  });

  it('returns null for a missing type', () => {
    expect(relationLabel(undefined)).toBeNull();
    expect(relationLabel(null)).toBeNull();
    expect(relationLabel('')).toBeNull();
  });

  it('falls back to a de-snaked label for an unrecognised type', () => {
    expect(relationLabel('mentions_in_passing')).toBe('mentions in passing');
  });
});

describe('isContradictionRelation', () => {
  it('is true for every contradiction relation', () => {
    expect(isContradictionRelation('contradicts')).toBe(true);
    expect(isContradictionRelation('disagrees_with')).toBe(true);
    expect(isContradictionRelation('conflicts_with')).toBe(true);
  });

  it('is false for ordinary relations and missing types', () => {
    expect(isContradictionRelation('depends_on')).toBe(false);
    expect(isContradictionRelation(undefined)).toBe(false);
    expect(isContradictionRelation(null)).toBe(false);
  });
});
