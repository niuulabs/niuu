import { describe, it, expect } from 'vitest';
import { KIND_GROUPS, kindGroup, kindLabel } from './memoryKinds';

const ENTITY_LEAF_KINDS = [
  'person',
  'project',
  'concept',
  'technology',
  'organization',
  'strategy',
];
const DIRECT_LEAF_KINDS = [
  'topic',
  'decision',
  'directive',
  'preference',
  'goal',
  'observation',
  'thread',
];

describe('kindGroup', () => {
  it('folds every entity sub-type (and the "entity" kind itself) into "entity"', () => {
    for (const kind of [...ENTITY_LEAF_KINDS, 'entity']) {
      expect(kindGroup(kind)).toBe('entity');
    }
  });

  it('keeps every direct-group kind as its own group', () => {
    for (const kind of DIRECT_LEAF_KINDS) {
      expect(kindGroup(kind)).toBe(kind);
    }
  });

  it('normalises case', () => {
    expect(kindGroup('Person')).toBe('entity');
    expect(kindGroup('DECISION')).toBe('decision');
  });

  it('falls back to "page" for unknown or missing kinds', () => {
    expect(kindGroup('bogus')).toBe('page');
    expect(kindGroup(undefined)).toBe('page');
  });
});

describe('kindLabel', () => {
  it('lowercases a given kind', () => {
    expect(kindLabel('Technology')).toBe('technology');
  });

  it('returns "page" when the kind is missing', () => {
    expect(kindLabel(undefined)).toBe('page');
  });
});

describe('KIND_GROUPS', () => {
  it('covers every group kindGroup can return, each with a label and singular form', () => {
    const ids = new Set(KIND_GROUPS.map((g) => g.id));
    for (const kind of [...DIRECT_LEAF_KINDS, ...ENTITY_LEAF_KINDS, 'bogus']) {
      expect(ids.has(kindGroup(kind))).toBe(true);
    }
    for (const group of KIND_GROUPS) {
      expect(typeof group.label).toBe('string');
      expect(typeof group.singular).toBe('string');
    }
  });

  it('includes the "page" group for untyped pages', () => {
    expect(KIND_GROUPS.some((g) => g.id === 'page')).toBe(true);
  });
});
