import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useFlockConfig } from './useFlockConfig';

const mockGet = vi.hoisted(() => vi.fn());
const mockPatch = vi.hoisted(() => vi.fn());

vi.mock('@/modules/shared/api/client', () => ({
  createApiClient: () => ({
    get: mockGet,
    patch: mockPatch,
  }),
}));

const successResponse = {
  flock_enabled: true,
  flock_default_personas: [],
  flock_llm_config: {},
  flock_sleipnir_publish_urls: [],
};

describe('useFlockConfig', () => {
  beforeEach(() => {
    mockGet.mockResolvedValue(successResponse);
    mockPatch.mockResolvedValue(successResponse);
  });

  it('starts with loading true and no error', () => {
    const { result } = renderHook(() => useFlockConfig());
    expect(result.current.loading).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it('loads config on mount', async () => {
    const { result } = renderHook(() => useFlockConfig());
    await act(async () => {});
    expect(result.current.loading).toBe(false);
    expect(result.current.config).toEqual(successResponse);
  });

  it('starts with not updating', () => {
    const { result } = renderHook(() => useFlockConfig());
    expect(result.current.updating).toBe(false);
  });

  it('setFlockEnabled resolves without error', async () => {
    const { result } = renderHook(() => useFlockConfig());
    await act(async () => {
      await result.current.setFlockEnabled(true);
    });
    expect(result.current.error).toBeNull();
    expect(result.current.updating).toBe(false);
  });

  it('setDefaultPersonas resolves without error', async () => {
    const { result } = renderHook(() => useFlockConfig());
    await act(async () => {
      await result.current.setDefaultPersonas(['coordinator', 'reviewer']);
    });
    expect(result.current.error).toBeNull();
  });

  it('setLlmConfig resolves without error', async () => {
    const { result } = renderHook(() => useFlockConfig());
    await act(async () => {
      await result.current.setLlmConfig({ model: 'claude-sonnet-4-6' });
    });
    expect(result.current.error).toBeNull();
  });

  it('setSleipnirUrls resolves without error', async () => {
    const { result } = renderHook(() => useFlockConfig());
    await act(async () => {
      await result.current.setSleipnirUrls(['http://sleipnir:4222']);
    });
    expect(result.current.error).toBeNull();
  });

  it('captures error on API failure', async () => {
    mockPatch.mockRejectedValueOnce(new Error('Network error'));

    const { result } = renderHook(() => useFlockConfig());
    await act(async () => {
      await result.current.setFlockEnabled(true);
    });
    expect(result.current.error).toBe('Network error');
    expect(result.current.updating).toBe(false);
  });
});
