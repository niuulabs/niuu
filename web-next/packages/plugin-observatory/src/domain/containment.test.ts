import { describe, it, expect } from 'vitest';
import { isDescendant, reparent } from './containment';
import type { Registry } from './index';

/**
 * Minimal registry fixture used across tests.
 *
 * Topology:
 *   realm
 *     └─ cluster
 *          └─ host
 *               └─ service
 *   device   (root, no parents)
 */
const makeRegistry = (): Registry => ({
  version: 1,
  updatedAt: '2026-01-01T00:00:00Z',
  types: [
    {
      id: 'realm',
      label: 'Realm',
      rune: 'ᛞ',
      icon: 'globe',
      shape: 'ring',
      color: 'ice-100',
      size: 18,
      border: 'solid',
      canContain: ['cluster'],
      parentTypes: [],
      category: 'topology',
      description: 'A realm.',
      fields: [],
    },
    {
      id: 'cluster',
      label: 'Cluster',
      rune: 'ᚲ',
      icon: 'layers',
      shape: 'ring-dashed',
      color: 'ice-200',
      size: 14,
      border: 'dashed',
      canContain: ['host'],
      parentTypes: ['realm'],
      category: 'topology',
      description: 'A cluster.',
      fields: [],
    },
    {
      id: 'host',
      label: 'Host',
      rune: 'ᚦ',
      icon: 'server',
      shape: 'rounded-rect',
      color: 'slate-400',
      size: 22,
      border: 'solid',
      canContain: ['service'],
      parentTypes: ['cluster'],
      category: 'hardware',
      description: 'A host.',
      fields: [],
    },
    {
      id: 'service',
      label: 'Service',
      rune: 'ᛦ',
      icon: 'box',
      shape: 'dot',
      color: 'ice-300',
      size: 8,
      border: 'solid',
      canContain: [],
      parentTypes: ['host'],
      category: 'infrastructure',
      description: 'A service.',
      fields: [],
    },
    {
      id: 'device',
      label: 'Device',
      rune: 'ᚠ',
      icon: 'wifi',
      shape: 'dot',
      color: 'slate-400',
      size: 5,
      border: 'dashed',
      canContain: [],
      parentTypes: [],
      category: 'device',
      description: 'A device.',
      fields: [],
    },
  ],
});

// ── isDescendant ──────────────────────────────────────────────────────────────

describe('isDescendant', () => {
  it('returns true when comparing a node to itself', () => {
    expect(isDescendant(makeRegistry(), 'realm', 'realm')).toBe(true);
  });

  it('returns true for a direct child', () => {
    expect(isDescendant(makeRegistry(), 'realm', 'cluster')).toBe(true);
  });

  it('returns true for a transitive descendant (depth-2)', () => {
    expect(isDescendant(makeRegistry(), 'realm', 'host')).toBe(true);
  });

  it('returns true for a transitive descendant (depth-3)', () => {
    expect(isDescendant(makeRegistry(), 'realm', 'service')).toBe(true);
  });

  it('returns false for an unrelated node', () => {
    expect(isDescendant(makeRegistry(), 'realm', 'device')).toBe(false);
  });

  it('returns false for the reverse direction (parent of ancestor)', () => {
    expect(isDescendant(makeRegistry(), 'cluster', 'realm')).toBe(false);
  });

  it('returns false when ancestorId does not exist', () => {
    expect(isDescendant(makeRegistry(), 'nonexistent', 'realm')).toBe(false);
  });

  it('handles cycles in canContain without infinite loop', () => {
    const r = makeRegistry();
    // Manually inject a cycle: service → realm (which is ancestor of service)
    r.types = r.types.map((t) => (t.id === 'service' ? { ...t, canContain: ['realm'] } : t));
    // Should still return without hanging; realm→service is still true
    expect(isDescendant(r, 'realm', 'service')).toBe(true);
    // And should not loop forever
    expect(isDescendant(r, 'service', 'device')).toBe(false);
  });
});

// ── reparent ──────────────────────────────────────────────────────────────────

describe('reparent', () => {
  it('adds childId to new parent canContain', () => {
    const r = reparent(makeRegistry(), 'service', 'realm');
    const realm = r.types.find((t) => t.id === 'realm')!;
    expect(realm.canContain).toContain('service');
  });

  it('removes childId from old parent canContain', () => {
    const r = reparent(makeRegistry(), 'service', 'realm');
    const host = r.types.find((t) => t.id === 'host')!;
    expect(host.canContain).not.toContain('service');
  });

  it('rewrites child parentTypes to single-parent array', () => {
    const r = reparent(makeRegistry(), 'service', 'realm');
    const service = r.types.find((t) => t.id === 'service')!;
    expect(service.parentTypes).toEqual(['realm']);
  });

  it('bumps the registry version by 1', () => {
    const before = makeRegistry();
    const after = reparent(before, 'service', 'realm');
    expect(after.version).toBe(before.version + 1);
  });

  it('sets updatedAt to a new ISO timestamp', () => {
    const before = makeRegistry();
    const after = reparent(before, 'service', 'realm');
    expect(after.updatedAt).not.toBe(before.updatedAt);
    expect(() => new Date(after.updatedAt)).not.toThrow();
  });

  it('does not mutate the source registry', () => {
    const before = makeRegistry();
    const snapshot = JSON.stringify(before);
    reparent(before, 'service', 'realm');
    expect(JSON.stringify(before)).toBe(snapshot);
  });

  it('does not duplicate childId if new parent already contains it', () => {
    const r = makeRegistry();
    // realm already contains cluster; reparenting cluster → realm again should be idempotent
    const after = reparent(r, 'cluster', 'realm');
    const realm = after.types.find((t) => t.id === 'realm')!;
    expect(realm.canContain.filter((id) => id === 'cluster').length).toBe(1);
  });

  it('leaves other types unchanged except version/updatedAt', () => {
    const before = makeRegistry();
    const after = reparent(before, 'service', 'realm');
    // cluster should be untouched aside from version on the registry root
    const beforeCluster = before.types.find((t) => t.id === 'cluster')!;
    const afterCluster = after.types.find((t) => t.id === 'cluster')!;
    expect(afterCluster).toEqual(beforeCluster);
  });

  it('moves a root type to become a child of another root', () => {
    // device has no parents; move it under realm
    const after = reparent(makeRegistry(), 'device', 'realm');
    const realm = after.types.find((t) => t.id === 'realm')!;
    const device = after.types.find((t) => t.id === 'device')!;
    expect(realm.canContain).toContain('device');
    expect(device.parentTypes).toEqual(['realm']);
  });
});
