import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { useVolundrSessions, useVolundrStats } from './useVolundrSessions';
import { createMockVolundrService } from '../adapters/mock';

function makeWrapper(service = createMockVolundrService()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ volundr: service }}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('useVolundrSessions', () => {
  it('returns sessions from the service', async () => {
    const { result } = renderHook(() => useVolundrSessions(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.length).toBeGreaterThan(0);
    expect(result.current.data?.[0]).toHaveProperty('id');
    expect(result.current.data?.[0]).toHaveProperty('status');
  });

  it('starts in loading state', () => {
    const { result } = renderHook(() => useVolundrSessions(), { wrapper: makeWrapper() });
    // Initially loading (before the first async tick resolves)
    expect(result.current.isLoading).toBe(true);
  });

  it('returns error when service fails', async () => {
    const failingService = {
      ...createMockVolundrService(),
      getSessions: async () => {
        throw new Error('session store down');
      },
    };
    const { result } = renderHook(() => useVolundrSessions(), {
      wrapper: makeWrapper(failingService),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
  });
});

describe('useVolundrStats', () => {
  it('returns stats from the service', async () => {
    const { result } = renderHook(() => useVolundrStats(), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(typeof result.current.data?.activeSessions).toBe('number');
    expect(typeof result.current.data?.tokensToday).toBe('number');
  });
});
