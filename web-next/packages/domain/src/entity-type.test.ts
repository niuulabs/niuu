import { describe, it, expect } from 'vitest';
import {
  entityShapeSchema,
  entityCategorySchema,
  entityTypeSchema,
  typeRegistrySchema,
} from './entity-type';

// ---------------------------------------------------------------------------
// entityShapeSchema
// ---------------------------------------------------------------------------

const validShapes = [
  'ring',
  'ring-dashed',
  'rounded-rect',
  'diamond',
  'triangle',
  'hex',
  'chevron',
  'square',
  'square-sm',
  'pentagon',
  'halo',
  'mimir',
  'mimir-small',
  'dot',
] as const;

describe('entityShapeSchema', () => {
  it.each(validShapes)('accepts shape "%s"', (shape) => {
    expect(entityShapeSchema.parse(shape)).toBe(shape);
  });

  it('rejects an unknown shape', () => {
    expect(() => entityShapeSchema.parse('blob')).toThrow();
  });
});

// ---------------------------------------------------------------------------
// entityCategorySchema
// ---------------------------------------------------------------------------

const validCategories = [
  'topology',
  'hardware',
  'agent',
  'coordinator',
  'knowledge',
  'infrastructure',
  'device',
  'composite',
] as const;

describe('entityCategorySchema', () => {
  it.each(validCategories)('accepts category "%s"', (cat) => {
    expect(entityCategorySchema.parse(cat)).toBe(cat);
  });

  it('rejects an unknown category', () => {
    expect(() => entityCategorySchema.parse('mystical')).toThrow();
  });
});

// ---------------------------------------------------------------------------
// entityTypeSchema
// ---------------------------------------------------------------------------

const ravenEntityType = {
  id: 'raven',
  label: 'Raven',
  category: 'agent',
  rune: 'ᚱ',
  shape: 'ring',
  color: '--brand-400',
  description: 'An autonomous agent dispatched to complete a raid.',
  parentTypes: ['cluster'],
  canContain: [],
} as const;

describe('entityTypeSchema', () => {
  it('round-trips a full entity type', () => {
    expect(entityTypeSchema.parse(ravenEntityType)).toEqual(ravenEntityType);
  });

  it('accepts empty parentTypes and canContain', () => {
    const result = entityTypeSchema.parse({ ...ravenEntityType, parentTypes: [], canContain: [] });
    expect(result.parentTypes).toEqual([]);
    expect(result.canContain).toEqual([]);
  });

  it('accepts multiple parentTypes and canContain entries', () => {
    const result = entityTypeSchema.parse({
      ...ravenEntityType,
      parentTypes: ['cluster', 'realm'],
      canContain: ['session', 'tool'],
    });
    expect(result.parentTypes).toHaveLength(2);
    expect(result.canContain).toHaveLength(2);
  });

  it('rejects empty id', () => {
    expect(() => entityTypeSchema.parse({ ...ravenEntityType, id: '' })).toThrow();
  });

  it('rejects empty label', () => {
    expect(() => entityTypeSchema.parse({ ...ravenEntityType, label: '' })).toThrow();
  });

  it('rejects empty rune', () => {
    expect(() => entityTypeSchema.parse({ ...ravenEntityType, rune: '' })).toThrow();
  });

  it('rejects a multi-character rune', () => {
    expect(() => entityTypeSchema.parse({ ...ravenEntityType, rune: 'AB' })).toThrow();
  });

  it('rejects invalid shape', () => {
    expect(() => entityTypeSchema.parse({ ...ravenEntityType, shape: 'blob' })).toThrow();
  });

  it('rejects invalid category', () => {
    expect(() => entityTypeSchema.parse({ ...ravenEntityType, category: 'mystical' })).toThrow();
  });

  it('rejects empty color', () => {
    expect(() => entityTypeSchema.parse({ ...ravenEntityType, color: '' })).toThrow();
  });

  it.each(validCategories)('accepts category "%s"', (cat) => {
    const result = entityTypeSchema.parse({ ...ravenEntityType, category: cat });
    expect(result.category).toBe(cat);
  });

  it.each(validShapes)('accepts shape "%s"', (shape) => {
    const result = entityTypeSchema.parse({ ...ravenEntityType, shape });
    expect(result.shape).toBe(shape);
  });
});

// ---------------------------------------------------------------------------
// typeRegistrySchema
// ---------------------------------------------------------------------------

const minimalRegistry = {
  version: 1,
  updatedAt: '2026-04-19T00:00:00Z',
  types: [ravenEntityType],
} as const;

describe('typeRegistrySchema', () => {
  it('round-trips a minimal registry', () => {
    const result = typeRegistrySchema.parse(minimalRegistry);
    expect(result.version).toBe(1);
    expect(result.types).toHaveLength(1);
    expect(result.types[0]?.id).toBe('raven');
  });

  it('round-trips an empty types array', () => {
    const result = typeRegistrySchema.parse({ ...minimalRegistry, types: [] });
    expect(result.types).toEqual([]);
  });

  it('round-trips version 0', () => {
    expect(typeRegistrySchema.parse({ ...minimalRegistry, version: 0 }).version).toBe(0);
  });

  it('rejects negative version', () => {
    expect(() => typeRegistrySchema.parse({ ...minimalRegistry, version: -1 })).toThrow();
  });

  it('rejects fractional version', () => {
    expect(() => typeRegistrySchema.parse({ ...minimalRegistry, version: 1.5 })).toThrow();
  });

  it('rejects empty updatedAt', () => {
    expect(() => typeRegistrySchema.parse({ ...minimalRegistry, updatedAt: '' })).toThrow();
  });

  it('rejects an invalid type in the types array', () => {
    expect(() =>
      typeRegistrySchema.parse({
        ...minimalRegistry,
        types: [{ ...ravenEntityType, id: '' }],
      }),
    ).toThrow();
  });

  it('round-trips a registry with multiple types', () => {
    const realmType = {
      id: 'realm',
      label: 'Realm',
      category: 'topology' as const,
      rune: 'ᚱ',
      shape: 'rounded-rect' as const,
      color: '--color-brand',
      description: 'A logical boundary containing clusters and hosts.',
      parentTypes: [],
      canContain: ['cluster', 'host'],
    };
    const result = typeRegistrySchema.parse({
      ...minimalRegistry,
      types: [ravenEntityType, realmType],
    });
    expect(result.types).toHaveLength(2);
  });
});
