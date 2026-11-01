import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useAmbient } from './useAmbient';

const STORAGE_KEY = 'niuu-login-ambient';

describe('useAmbient', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('returns "topology" as the default when nothing is stored', () => {
    const { result } = renderHook(() => useAmbient());
    expect(result.current[0]).toBe('topology');
  });

  it('reads an existing valid value from localStorage', () => {
    localStorage.setItem(STORAGE_KEY, 'constellation');
    const { result } = renderHook(() => useAmbient());
    expect(result.current[0]).toBe('constellation');
  });

  it('reads "lattice" from localStorage', () => {
    localStorage.setItem(STORAGE_KEY, 'lattice');
    const { result } = renderHook(() => useAmbient());
    expect(result.current[0]).toBe('lattice');
  });

  it('falls back to "topology" when stored value is invalid', () => {
    localStorage.setItem(STORAGE_KEY, 'invalid-value');
    const { result } = renderHook(() => useAmbient());
    expect(result.current[0]).toBe('topology');
  });

  it('falls back to "topology" when stored value is empty string', () => {
    localStorage.setItem(STORAGE_KEY, '');
    const { result } = renderHook(() => useAmbient());
    expect(result.current[0]).toBe('topology');
  });

  it('persists the new value to localStorage when setAmbient is called', () => {
    const { result } = renderHook(() => useAmbient());
    act(() => {
      result.current[1]('constellation');
    });
    expect(localStorage.getItem(STORAGE_KEY)).toBe('constellation');
  });

  it('updates the returned ambient when setAmbient is called', () => {
    const { result } = renderHook(() => useAmbient());
    act(() => {
      result.current[1]('lattice');
    });
    expect(result.current[0]).toBe('lattice');
  });

  it('persists through subsequent setAmbient calls', () => {
    const { result } = renderHook(() => useAmbient());
    act(() => {
      result.current[1]('constellation');
    });
    act(() => {
      result.current[1]('topology');
    });
    expect(result.current[0]).toBe('topology');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('topology');
  });

  it('handles localStorage being unavailable (read)', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('localStorage unavailable');
    });
    const { result } = renderHook(() => useAmbient());
    expect(result.current[0]).toBe('topology');
    getItem.mockRestore();
  });

  it('handles localStorage being unavailable (write)', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('localStorage unavailable');
    });
    const { result } = renderHook(() => useAmbient());
    // Should not throw
    act(() => {
      result.current[1]('lattice');
    });
    expect(result.current[0]).toBe('lattice');
    setItem.mockRestore();
  });
});
