import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useForgePreference } from './useForgePreference';

describe('Forge preferences', () => {
  beforeEach(() => localStorage.clear());
  it('validates stored values and synchronizes mounted controls', () => {
    localStorage.setItem('niuu.forge.test-filter', 'invalid');
    const first = renderHook(() => useForgePreference('test-filter', 'live', ['live', 'all']));
    const second = renderHook(() => useForgePreference('test-filter', 'live', ['live', 'all']));
    expect(first.result.current[0]).toBe('live');
    act(() => first.result.current[1]('all'));
    expect(second.result.current[0]).toBe('all');
    expect(localStorage.getItem('niuu.forge.test-filter')).toBe('all');
    act(() => {
      localStorage.setItem('niuu.forge.test-filter', 'live');
      window.dispatchEvent(new Event('storage'));
    });
    expect(first.result.current[0]).toBe('live');
  });
  it('keeps a control usable when browser storage is disabled', () => {
    const read = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('disabled');
    });
    const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('disabled');
    });
    const { result } = renderHook(() =>
      useForgePreference('storage-disabled-test', '0', ['0', '1']),
    );
    expect(result.current[0]).toBe('0');
    act(() => result.current[1]('1'));
    expect(result.current[0]).toBe('1');
    read.mockRestore();
    write.mockRestore();
  });
});
