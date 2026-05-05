import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useRegistryEditor } from './useRegistryEditor';
import type { Registry } from '../domain';

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
      canContain: [],
      parentTypes: ['cluster'],
      category: 'hardware',
      description: 'A host.',
      fields: [],
    },
  ],
});

describe('useRegistryEditor', () => {
  it('initialises selectedId to the first type', () => {
    const { result } = renderHook(() => useRegistryEditor(makeRegistry()));
    expect(result.current.selectedId).toBe('realm');
  });

  it('select() updates selectedId', () => {
    const { result } = renderHook(() => useRegistryEditor(makeRegistry()));
    act(() => result.current.select('cluster'));
    expect(result.current.selectedId).toBe('cluster');
  });

  it('select(null) clears selectedId', () => {
    const { result } = renderHook(() => useRegistryEditor(makeRegistry()));
    act(() => result.current.select(null));
    expect(result.current.selectedId).toBeNull();
  });

  it('tryReparent performs a valid reparent and returns true', () => {
    const { result } = renderHook(() => useRegistryEditor(makeRegistry()));
    let success: boolean;
    act(() => {
      success = result.current.tryReparent('host', 'realm');
    });
    expect(success!).toBe(true);
    const realm = result.current.registry.types.find((t) => t.id === 'realm')!;
    expect(realm.canContain).toContain('host');
    const host = result.current.registry.types.find((t) => t.id === 'host')!;
    expect(host.parentTypes).toEqual(['realm']);
  });

  it('tryReparent bumps version after a valid move', () => {
    const { result } = renderHook(() => useRegistryEditor(makeRegistry()));
    const before = result.current.registry.version;
    act(() => result.current.tryReparent('host', 'realm'));
    expect(result.current.registry.version).toBe(before + 1);
  });

  it('tryReparent returns false and does not change registry when childId === newParentId', () => {
    const { result } = renderHook(() => useRegistryEditor(makeRegistry()));
    const before = JSON.stringify(result.current.registry);
    let success: boolean;
    act(() => {
      success = result.current.tryReparent('realm', 'realm');
    });
    expect(success!).toBe(false);
    expect(JSON.stringify(result.current.registry)).toBe(before);
  });

  it('tryReparent returns false when the move would create a cycle', () => {
    // realm → cluster → host; moving realm under host would be a cycle
    const { result } = renderHook(() => useRegistryEditor(makeRegistry()));
    const before = JSON.stringify(result.current.registry);
    let success: boolean;
    act(() => {
      success = result.current.tryReparent('realm', 'host');
    });
    expect(success!).toBe(false);
    expect(JSON.stringify(result.current.registry)).toBe(before);
  });
});
