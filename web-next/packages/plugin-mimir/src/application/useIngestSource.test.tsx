import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it, vi } from 'vitest';
import { useIngestSource } from './useIngestSource';
import { createFakeMimirService } from '../testing/fakeMimirService';
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

describe('useIngestSource', () => {
  it('ingests a URL and calls onSuccess with the new source', async () => {
    const onSuccess = vi.fn();
    const { result } = renderWithService(
      () => useIngestSource(onSuccess),
      createFakeMimirService(),
    );
    act(() => result.current.ingest({ type: 'url', url: 'https://example.com/a' }));
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.originUrl).toBe('https://example.com/a');
    expect(onSuccess).toHaveBeenCalledWith(
      expect.objectContaining({ originUrl: 'https://example.com/a' }),
    );
  });

  it('ingests a file', async () => {
    const { result } = renderWithService(() => useIngestSource(), createFakeMimirService());
    const file = new File(['content'], 'notes.md');
    act(() => result.current.ingest({ type: 'file', file }));
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.title).toBe('notes.md');
  });

  it('surfaces an ingest error', async () => {
    const { result } = renderWithService(
      () => useIngestSource(),
      createFakeMimirService({ ingestShouldFail: true }),
    );
    act(() => result.current.ingest({ type: 'url', url: 'https://example.com/a' }));
    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it('reset clears mutation state', async () => {
    const { result } = renderWithService(() => useIngestSource(), createFakeMimirService());
    act(() => result.current.ingest({ type: 'url', url: 'https://example.com/a' }));
    await waitFor(() => expect(result.current.data).toBeDefined());
    act(() => result.current.reset());
    await waitFor(() => expect(result.current.data).toBeUndefined());
  });
});
