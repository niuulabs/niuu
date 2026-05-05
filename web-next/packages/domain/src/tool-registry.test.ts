import { describe, it, expect } from 'vitest';
import { toolGroupSchema, toolSchema, toolRegistrySchema } from './tool-registry';

// ---------------------------------------------------------------------------
// toolGroupSchema
// ---------------------------------------------------------------------------

describe('toolGroupSchema', () => {
  it.each(['fs', 'shell', 'git', 'mimir', 'observe', 'security', 'bus'])(
    'accepts group "%s"',
    (group) => {
      expect(toolGroupSchema.parse(group)).toBe(group);
    },
  );

  it('rejects an unknown group', () => {
    expect(() => toolGroupSchema.parse('network')).toThrow();
  });
});

// ---------------------------------------------------------------------------
// toolSchema
// ---------------------------------------------------------------------------

const readTool = {
  id: 'read',
  group: 'fs',
  destructive: false,
  desc: 'Read a file from the filesystem',
} as const;

const bashTool = {
  id: 'bash',
  group: 'shell',
  destructive: true,
  desc: 'Execute a shell command',
} as const;

describe('toolSchema', () => {
  it('round-trips a non-destructive tool', () => {
    expect(toolSchema.parse(readTool)).toEqual(readTool);
  });

  it('round-trips a destructive tool', () => {
    expect(toolSchema.parse(bashTool)).toEqual(bashTool);
  });

  it('rejects empty id', () => {
    expect(() => toolSchema.parse({ ...readTool, id: '' })).toThrow();
  });

  it('rejects empty desc', () => {
    expect(() => toolSchema.parse({ ...readTool, desc: '' })).toThrow();
  });

  it('rejects invalid group', () => {
    expect(() => toolSchema.parse({ ...readTool, group: 'network' })).toThrow();
  });

  it('rejects non-boolean destructive', () => {
    expect(() => toolSchema.parse({ ...readTool, destructive: 'yes' })).toThrow();
  });

  it.each(['fs', 'shell', 'git', 'mimir', 'observe', 'security', 'bus'])(
    'accepts tool in group "%s"',
    (group) => {
      const t = toolSchema.parse({ id: `tool.${group}`, group, destructive: false, desc: 'x' });
      expect(t.group).toBe(group);
    },
  );
});

// ---------------------------------------------------------------------------
// toolRegistrySchema
// ---------------------------------------------------------------------------

describe('toolRegistrySchema', () => {
  it('round-trips an empty registry', () => {
    expect(toolRegistrySchema.parse([])).toEqual([]);
  });

  it('round-trips a populated registry', () => {
    const registry = [readTool, bashTool];
    const result = toolRegistrySchema.parse(registry);
    expect(result).toHaveLength(2);
    expect(result[0]).toEqual(readTool);
    expect(result[1]).toEqual(bashTool);
  });

  it('rejects non-array input', () => {
    expect(() => toolRegistrySchema.parse(null)).toThrow();
  });

  it('rejects an array with an invalid tool', () => {
    expect(() => toolRegistrySchema.parse([{ ...readTool, id: '' }])).toThrow();
  });
});
