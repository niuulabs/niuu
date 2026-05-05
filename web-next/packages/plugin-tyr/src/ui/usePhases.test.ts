import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createElement } from 'react';
import type { ReactNode } from 'react';
import { usePhases } from './usePhases';
import type { Phase } from '../domain/saga';

const MOCK_PHASE: Phase = {
  id: '00000000-0000-0000-0000-000000000100',
  sagaId: '00000000-0000-0000-0000-000000000001',
  trackerId: 'NIU-M1',
  number: 1,
  name: 'Phase 1: Foundation',
  status: 'complete',
  confidence: 90,
  raids: [],
};

function makeWrapper(service: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(
      QueryClientProvider,
      { client },
      createElement(ServicesProvider, { services: service }, children),
    );
  };
}

describe('usePhases', () => {
  it('returns phases for a given saga ID', async () => {
    const svc = { getPhases: vi.fn().mockResolvedValue([MOCK_PHASE]) };
    const { result } = renderHook(() => usePhases('00000000-0000-0000-0000-000000000001'), {
      wrapper: makeWrapper({ tyr: svc }),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data?.[0]?.name).toBe('Phase 1: Foundation');
    expect(svc.getPhases).toHaveBeenCalledWith('00000000-0000-0000-0000-000000000001');
  });

  it('does not fetch when sagaId is null', () => {
    const svc = { getPhases: vi.fn() };
    const { result } = renderHook(() => usePhases(null), {
      wrapper: makeWrapper({ tyr: svc }),
    });
    expect(result.current.isLoading).toBe(false);
    expect(svc.getPhases).not.toHaveBeenCalled();
  });

  it('does not fetch when sagaId is undefined', () => {
    const svc = { getPhases: vi.fn() };
    const { result } = renderHook(() => usePhases(undefined), {
      wrapper: makeWrapper({ tyr: svc }),
    });
    expect(result.current.isLoading).toBe(false);
    expect(svc.getPhases).not.toHaveBeenCalled();
  });

  it('enters error state when service rejects', async () => {
    const svc = { getPhases: vi.fn().mockRejectedValue(new Error('phases unavailable')) };
    const { result } = renderHook(() => usePhases('00000000-0000-0000-0000-000000000001'), {
      wrapper: makeWrapper({ tyr: svc }),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
  });

  it('starts in loading state when sagaId is present', () => {
    const svc = { getPhases: vi.fn().mockReturnValue(new Promise(() => undefined)) };
    const { result } = renderHook(() => usePhases('00000000-0000-0000-0000-000000000001'), {
      wrapper: makeWrapper({ tyr: svc }),
    });
    expect(result.current.isLoading).toBe(true);
  });
});
