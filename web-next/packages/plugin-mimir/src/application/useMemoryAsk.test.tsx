import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it } from 'vitest';
import { useMemoryAsk } from './useMemoryAsk';
import { createFakeMimirService, FAKE_GRAPH, fakeNodeId } from '../testing/fakeMimirService';
import type { IMimirService } from '../ports';
import type { MimirGraph } from '../domain/api-types';

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

describe('useMemoryAsk', () => {
  it('does not query for a blank question', () => {
    const { result } = renderWithService(
      () => useMemoryAsk('   ', undefined, FAKE_GRAPH),
      createFakeMimirService(),
    );
    expect(result.current.isLoading).toBe(false);
    expect(result.current.results).toEqual([]);
    expect(result.current.answers).toEqual([]);
  });

  it('quotes verbatim facts from the top answering pages and maps them to graph node ids', async () => {
    const { result } = renderWithService(
      () => useMemoryAsk('gateway', undefined, FAKE_GRAPH),
      createFakeMimirService(),
    );
    expect(result.current.isLoading).toBe(true);
    await waitFor(() => expect(result.current.quoted.length).toBeGreaterThan(0));
    expect(result.current.quoted[0]!.fact).toBe(
      'The route tables live in values-cedar.yaml, which wins over values-niuu.yaml.',
    );
    expect(result.current.answers[0]).toEqual({
      nodeId: fakeNodeId('platform', '/platform/gateway-routing'),
      n: 1,
    });
    expect(result.current.rankByPath.get('/platform/gateway-routing')).toBe(1);
    expect(result.current.nodeIdByPath.get('/platform/gateway-routing')).toBe(
      fakeNodeId('platform', '/platform/gateway-routing'),
    );
    await waitFor(() => expect(result.current.elapsedSeconds).not.toBeNull());
  });

  it('scopes the search and node lookup by mount when given', async () => {
    const { result } = renderWithService(
      () => useMemoryAsk('gateway', 'shared', FAKE_GRAPH),
      createFakeMimirService(),
    );
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.results).toEqual([]);
    expect(result.current.answers).toEqual([]);
  });

  it('drops an answer whose page has no matching graph node', async () => {
    const emptyGraph: MimirGraph = { nodes: [], edges: [] };
    const { result } = renderWithService(
      () => useMemoryAsk('gateway', undefined, emptyGraph),
      createFakeMimirService(),
    );
    await waitFor(() => expect(result.current.results.length).toBeGreaterThan(0));
    expect(result.current.answers).toEqual([]);
    expect(result.current.nodeIdByPath.size).toBe(0);
    // rankByPath is purely positional and still fills in, independent of node resolution.
    expect(result.current.rankByPath.get('/platform/gateway-routing')).toBe(1);
  });

  it('surfaces a search error', async () => {
    const base = createFakeMimirService();
    const failing: IMimirService = {
      ...base,
      pages: {
        ...base.pages,
        search: async () => {
          throw new Error('search down');
        },
      },
    };
    const { result } = renderWithService(
      () => useMemoryAsk('gateway', undefined, FAKE_GRAPH),
      failing,
    );
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
