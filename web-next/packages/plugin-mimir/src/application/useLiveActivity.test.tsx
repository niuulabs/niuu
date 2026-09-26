import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it } from 'vitest';
import { useLiveActivity, LIVE_ACTIVITY_POLL_MS } from './useLiveActivity';
import { createFakeMimirService, FAKE_LIVE_ACTIVITY } from '../testing/fakeMimirService';
import type { IMimirService } from '../ports';

function renderWithService<T>(callback: () => T, service: IMimirService) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderHook(callback, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>
        <ServicesProvider services={{ mimir: service }}>{children}</ServicesProvider>
      </QueryClientProvider>
    ),
  });
}

describe('useLiveActivity', () => {
  it('polls on a fixed interval', () => {
    expect(LIVE_ACTIVITY_POLL_MS).toBeGreaterThan(0);
  });

  it('loads the live activity feed', async () => {
    const { result } = renderWithService(() => useLiveActivity(), createFakeMimirService());
    expect(result.current.isLoading).toBe(true);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(FAKE_LIVE_ACTIVITY);
  });

  it('surfaces a service error', async () => {
    const base = createFakeMimirService();
    const failing: IMimirService = {
      ...base,
      pages: {
        ...base.pages,
        getLiveActivity: async () => {
          throw new Error('feed unavailable');
        },
      },
    };
    const { result } = renderWithService(() => useLiveActivity(), failing);
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
