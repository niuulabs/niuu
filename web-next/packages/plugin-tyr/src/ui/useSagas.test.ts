import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { ReactNode } from 'react';
import { createElement } from 'react';
import { useSagas } from './useSagas';
import type { Saga } from '../domain/saga';

const sampleSaga: Saga = {
  id: '00000000-0000-0000-0000-000000000001',
  trackerId: 'NIU-001',
  trackerType: 'linear',
  slug: 'auth-rewrite',
  name: 'Auth Rewrite',
  repos: ['niuulabs/volundr'],
  featureBranch: 'feat/auth-rewrite',
  status: 'active',
  confidence: 80,
  createdAt: '2026-01-01T00:00:00Z',
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

describe('useSagas', () => {
  it('returns sagas list from the service', async () => {
    const svc = { getSagas: vi.fn().mockResolvedValue([sampleSaga]) };

    const { result } = renderHook(() => useSagas(), {
      wrapper: makeWrapper({ tyr: svc }),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data?.[0]?.name).toBe('Auth Rewrite');
    expect(svc.getSagas).toHaveBeenCalled();
  });

  it('enters error state when service rejects', async () => {
    const svc = {
      getSagas: vi.fn().mockRejectedValue(new Error('service unavailable')),
    };

    const { result } = renderHook(() => useSagas(), {
      wrapper: makeWrapper({ tyr: svc }),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
  });

  it('starts in loading state', () => {
    const svc = {
      getSagas: vi.fn().mockReturnValue(new Promise(() => undefined)),
    };

    const { result } = renderHook(() => useSagas(), {
      wrapper: makeWrapper({ tyr: svc }),
    });

    expect(result.current.isLoading).toBe(true);
  });
});
