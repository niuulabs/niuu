import { describe, it, expect } from 'vitest';
import { mountRoleSchema, mountStatusSchema, mountSchema } from './mount';

// ---------------------------------------------------------------------------
// mountRoleSchema
// ---------------------------------------------------------------------------

describe('mountRoleSchema', () => {
  it.each(['local', 'shared', 'domain'])('accepts role "%s"', (role) => {
    expect(mountRoleSchema.parse(role)).toBe(role);
  });

  it('rejects an unknown role', () => {
    expect(() => mountRoleSchema.parse('remote')).toThrow();
  });
});

// ---------------------------------------------------------------------------
// mountStatusSchema
// ---------------------------------------------------------------------------

describe('mountStatusSchema', () => {
  it.each(['healthy', 'degraded', 'down'])('accepts status "%s"', (s) => {
    expect(mountStatusSchema.parse(s)).toBe(s);
  });

  it('rejects an unknown status', () => {
    expect(() => mountStatusSchema.parse('unknown')).toThrow();
  });
});

// ---------------------------------------------------------------------------
// mountSchema
// ---------------------------------------------------------------------------

const validMount = {
  name: 'primary',
  role: 'local',
  host: 'localhost',
  url: 'http://localhost:7891',
  priority: 0,
  categories: null,
  status: 'healthy',
  pages: 120,
  sources: 45,
  lintIssues: 3,
  lastWrite: '2026-04-18T10:00:00Z',
  embedding: 'sentence-transformers/all-MiniLM-L6-v2',
  sizeKb: 2048,
  desc: 'Primary local mount',
} as const;

describe('mountSchema', () => {
  it('round-trips a full mount', () => {
    const result = mountSchema.parse(validMount);
    expect(result).toEqual(validMount);
  });

  it('accepts null categories (accepts all)', () => {
    expect(mountSchema.parse({ ...validMount, categories: null }).categories).toBeNull();
  });

  it('accepts a non-null category list', () => {
    const result = mountSchema.parse({ ...validMount, categories: ['code', 'docs'] });
    expect(result.categories).toEqual(['code', 'docs']);
  });

  it('accepts an empty category list', () => {
    expect(mountSchema.parse({ ...validMount, categories: [] }).categories).toEqual([]);
  });

  it('rejects priority < 0', () => {
    expect(() => mountSchema.parse({ ...validMount, priority: -1 })).toThrow();
  });

  it('rejects negative pages count', () => {
    expect(() => mountSchema.parse({ ...validMount, pages: -1 })).toThrow();
  });

  it('rejects negative sources count', () => {
    expect(() => mountSchema.parse({ ...validMount, sources: -1 })).toThrow();
  });

  it('rejects negative lintIssues count', () => {
    expect(() => mountSchema.parse({ ...validMount, lintIssues: -1 })).toThrow();
  });

  it('rejects negative sizeKb', () => {
    expect(() => mountSchema.parse({ ...validMount, sizeKb: -1 })).toThrow();
  });

  it('rejects empty name', () => {
    expect(() => mountSchema.parse({ ...validMount, name: '' })).toThrow();
  });

  it('rejects invalid status', () => {
    expect(() => mountSchema.parse({ ...validMount, status: 'exploded' })).toThrow();
  });

  it('rejects invalid role', () => {
    expect(() => mountSchema.parse({ ...validMount, role: 'remote' })).toThrow();
  });

  it('accepts degraded and down statuses', () => {
    expect(mountSchema.parse({ ...validMount, status: 'degraded' }).status).toBe('degraded');
    expect(mountSchema.parse({ ...validMount, status: 'down' }).status).toBe('down');
  });

  it('accepts shared and domain roles', () => {
    expect(mountSchema.parse({ ...validMount, role: 'shared' }).role).toBe('shared');
    expect(mountSchema.parse({ ...validMount, role: 'domain' }).role).toBe('domain');
  });

  it('accepts zero pages and sources', () => {
    const result = mountSchema.parse({ ...validMount, pages: 0, sources: 0, lintIssues: 0 });
    expect(result.pages).toBe(0);
  });

  it('rejects fractional priority', () => {
    expect(() => mountSchema.parse({ ...validMount, priority: 1.5 })).toThrow();
  });
});
