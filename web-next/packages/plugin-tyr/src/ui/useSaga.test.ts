import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createElement } from 'react';
import type { ReactNode } from 'react';
import { useSaga } from './useSaga';
import type { Saga } from '../domain/saga';

const MOCK_SAGA: Saga = {
  id: '00000000-0000-0000-0000-000000000001',
  trackerId: 'NIU-500',
  trackerType: 'linear',
  slug: 'auth-rewrite',
  name: 'Auth Rewrite',
  repos: ['niuulabs/volundr'],
  featureBranch: 'feat/auth-rewrite',
  status: 'active',
  confidence: 82,
  createdAt: '2026-01-10T09:00:00Z',
  phaseSummary: { total: 3, completed: 1 },
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

describe('useSaga', () => {
  it('returns saga data from the service', async () => {
    const svc = { getSaga: vi.fn().mockResolvedValue(MOCK_SAGA) };
    const { result } = renderHook(() => useSaga(MOCK_SAGA.id), {
      wrapper: makeWrapper({ tyr: svc }),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(MOCK_SAGA);
    expect(svc.getSaga).toHaveBeenCalledWith(MOCK_SAGA.id);
  });

  it('returns null when saga is not found', async () => {
    const svc = { getSaga: vi.fn().mockResolvedValue(null) };
    const { result } = renderHook(() => useSaga('nonexistent'), {
      wrapper: makeWrapper({ tyr: svc }),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });

  it('enters error state when service rejects', async () => {
    const svc = { getSaga: vi.fn().mockRejectedValue(new Error('saga unavailable')) };
    const { result } = renderHook(() => useSaga(MOCK_SAGA.id), {
      wrapper: makeWrapper({ tyr: svc }),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
  });

  it('starts in loading state', () => {
    const svc = { getSaga: vi.fn().mockReturnValue(new Promise(() => undefined)) };
    const { result } = renderHook(() => useSaga(MOCK_SAGA.id), {
      wrapper: makeWrapper({ tyr: svc }),
    });
    expect(result.current.isLoading).toBe(true);
  });

  it('is disabled when id is empty string', () => {
    const svc = { getSaga: vi.fn() };
    const { result } = renderHook(() => useSaga(''), {
      wrapper: makeWrapper({ tyr: svc }),
    });
    expect(result.current.fetchStatus).toBe('idle');
    expect(svc.getSaga).not.toHaveBeenCalled();
  });
});
