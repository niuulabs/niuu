import { describe, expect, it } from 'vitest';
import { isContradictionRelation, relationLabel } from './relationLabel';

describe('relationLabel', () => {
  it('turns a typed relation into words', () => {
    expect(relationLabel('depends_on')).toBe('depends on');
    expect(relationLabel('explains')).toBe('explains');
    expect(relationLabel('disagrees_with')).toBe('disagrees with');
  });

  it.each(['wikilink', 'link', 'shared_source', 'related_entity'])(
    'has no label for %s',
    (type) => {
      expect(relationLabel(type)).toBeNull();
    },
  );

  it('has no label for a missing type', () => {
    expect(relationLabel(null)).toBeNull();
    expect(relationLabel(undefined)).toBeNull();
    expect(relationLabel('')).toBeNull();
  });
});

describe('isContradictionRelation', () => {
  it.each(['contradicts', 'disagrees_with', 'conflicts_with'])('is true for %s', (type) => {
    expect(isContradictionRelation(type)).toBe(true);
  });

  it('is false for other and missing relations', () => {
    expect(isContradictionRelation('depends_on')).toBe(false);
    expect(isContradictionRelation(null)).toBe(false);
    expect(isContradictionRelation(undefined)).toBe(false);
  });
});
