import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it } from 'vitest';
import { useMemoryGraph } from './useMemoryGraph';
import { createFakeMimirService, FAKE_KNOWLEDGE_GRAPH } from '../testing/fakeMimirService';
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

describe('useMemoryGraph', () => {
  it('loads the knowledge graph of every readable layer', async () => {
    const { result } = renderWithService(() => useMemoryGraph(), createFakeMimirService());
    expect(result.current.isLoading).toBe(true);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(FAKE_KNOWLEDGE_GRAPH);
  });

  it('surfaces a service error', async () => {
    const base = createFakeMimirService();
    const failing: IMimirService = {
      ...base,
      pages: {
        ...base.pages,
        getKnowledgeGraph: async () => {
          throw new Error('graph unavailable');
        },
      },
    };
    const { result } = renderWithService(() => useMemoryGraph(), failing);
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error?.message).toBe('graph unavailable');
  });
});
