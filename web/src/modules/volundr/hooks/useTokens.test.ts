import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useTokens } from './useTokens';
import type { IVolundrService } from '@/modules/volundr/ports';

function createMockService(overrides: Partial<IVolundrService> = {}): IVolundrService {
  return {
    listTokens: vi.fn().mockResolvedValue([]),
    createToken: vi.fn().mockResolvedValue({
      id: 'pat-1',
      name: 'test',
      token: 'pat_value',
      createdAt: '2025-01-01',
    }),
    revokeToken: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  } as unknown as IVolundrService;
}

describe('useTokens', () => {
  let service: IVolundrService;

  beforeEach(() => {
    vi.clearAllMocks();
    service = createMockService();
  });

  it('fetches tokens on mount', async () => {
    const mockTokens = [{ id: 'pat-1', name: 'My Token', lastUsed: null, createdAt: '2025-01-01' }];
    service = createMockService({
      listTokens: vi.fn().mockResolvedValue(mockTokens),
    });

    const { result } = renderHook(() => useTokens(service));

    expect(result.current.loading).toBe(true);

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.tokens).toEqual(mockTokens);
    expect(result.current.error).toBeNull();
  });

  it('sets error when listTokens fails with Error', async () => {
    service = createMockService({
      listTokens: vi.fn().mockRejectedValue(new Error('Network timeout')),
    });

    const { result } = renderHook(() => useTokens(service));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toBe('Network timeout');
    expect(result.current.tokens).toEqual([]);
  });

  it('sets generic error when listTokens fails with non-Error', async () => {
    service = createMockService({
      listTokens: vi.fn().mockRejectedValue('some string error'),
    });

    const { result } = renderHook(() => useTokens(service));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toBe('Failed to load tokens');
  });

  it('createToken delegates to service', async () => {
    const { result } = renderHook(() => useTokens(service));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    const pat = await act(() => result.current.createToken('My PAT'));

    expect(service.createToken).toHaveBeenCalledWith('My PAT');
    expect(pat.name).toBe('test');
  });

  it('revokeToken delegates to service and refreshes', async () => {
    const { result } = renderHook(() => useTokens(service));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(() => result.current.revokeToken('pat-1'));

    expect(service.revokeToken).toHaveBeenCalledWith('pat-1');
    // listTokens should be called twice: once on mount, once after revoke
    expect(service.listTokens).toHaveBeenCalledTimes(2);
  });

  it('refresh re-fetches tokens', async () => {
    const { result } = renderHook(() => useTokens(service));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(() => result.current.refresh());

    expect(service.listTokens).toHaveBeenCalledTimes(2);
  });
});
