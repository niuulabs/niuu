import { describe, it, expect } from 'vitest';
import { fieldTypeSchema, eventSpecSchema, eventCatalogSchema } from './event-catalog';

// ---------------------------------------------------------------------------
// fieldTypeSchema
// ---------------------------------------------------------------------------

describe('fieldTypeSchema', () => {
  it.each(['string', 'number', 'boolean', 'object', 'array', 'any'])(
    'accepts field type "%s"',
    (t) => {
      expect(fieldTypeSchema.parse(t)).toBe(t);
    },
  );

  it('rejects an unknown field type', () => {
    expect(() => fieldTypeSchema.parse('date')).toThrow();
  });
});

// ---------------------------------------------------------------------------
// eventSpecSchema
// ---------------------------------------------------------------------------

describe('eventSpecSchema', () => {
  it('round-trips a full event spec', () => {
    const input = {
      name: 'build.artifact',
      schema: { url: 'string', size: 'number', success: 'boolean' },
    };
    expect(eventSpecSchema.parse(input)).toEqual(input);
  });

  it('round-trips a zero-payload event', () => {
    const input = { name: 'ping', schema: {} };
    expect(eventSpecSchema.parse(input)).toEqual(input);
  });

  it('rejects an empty event name', () => {
    expect(() => eventSpecSchema.parse({ name: '', schema: {} })).toThrow();
  });

  it('rejects an invalid field type in schema', () => {
    expect(() => eventSpecSchema.parse({ name: 'x', schema: { ts: 'Date' } })).toThrow();
  });

  it('accepts all valid field types in a schema', () => {
    const schema = {
      a: 'string',
      b: 'number',
      c: 'boolean',
      d: 'object',
      e: 'array',
      f: 'any',
    };
    const result = eventSpecSchema.parse({ name: 'multi', schema });
    expect(result.schema).toEqual(schema);
  });
});

// ---------------------------------------------------------------------------
// eventCatalogSchema
// ---------------------------------------------------------------------------

describe('eventCatalogSchema', () => {
  it('round-trips an empty catalog', () => {
    expect(eventCatalogSchema.parse([])).toEqual([]);
  });

  it('round-trips a catalog with multiple events', () => {
    const catalog = [
      { name: 'code.changed', schema: { path: 'string' } },
      { name: 'review.approved', schema: { reviewer: 'string', confidence: 'number' } },
      { name: 'ping', schema: {} },
    ];
    const result = eventCatalogSchema.parse(catalog);
    expect(result).toHaveLength(3);
    expect(result[0]?.name).toBe('code.changed');
    expect(result[2]?.name).toBe('ping');
  });

  it('rejects non-array input', () => {
    expect(() => eventCatalogSchema.parse({})).toThrow();
  });

  it('rejects a catalog with an invalid event', () => {
    expect(() => eventCatalogSchema.parse([{ name: '', schema: {} }])).toThrow();
  });
});
