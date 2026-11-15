import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it, vi } from 'vitest';
import { useMemorySearch } from './useMemoryGateway';
import { createMemoryGatewayMockAdapter } from '../adapters/memoryGateway';
import { MEMORY_GATEWAY_SERVICE_KEY, type IMemoryGatewayService } from '../ports/IMemoryGateway';

function renderSearch(
  query: string,
  projectId: string | undefined,
  gateway: IMemoryGatewayService = createMemoryGatewayMockAdapter(),
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderHook(() => useMemorySearch(query, projectId), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ [MEMORY_GATEWAY_SERVICE_KEY]: gateway }}>
          {children}
        </ServicesProvider>
      </QueryClientProvider>
    ),
  });
}

describe('useMemorySearch', () => {
  it('does not search for a blank query', () => {
    const search = vi.fn();
    const { result } = renderSearch('   ', undefined, {
      ...createMemoryGatewayMockAdapter(),
      search,
    });
    expect(result.current.isLoading).toBe(false);
    expect(result.current.data).toBeUndefined();
    expect(search).not.toHaveBeenCalled();
  });

  it('loads, then returns the hits', async () => {
    const { result } = renderSearch(' cut-over ', undefined);
    expect(result.current.isLoading).toBe(true);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.hits).toHaveLength(2);
  });

  it('narrows to one project and keys on both', async () => {
    const search = vi.fn().mockResolvedValue({ query: 'cut-over', hits: [] });
    const { result } = renderSearch('cut-over', 'p-1', {
      ...createMemoryGatewayMockAdapter(),
      search,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(search).toHaveBeenCalledWith('cut-over', { projectId: 'p-1' });
  });

  it('reports a failed search as an error', async () => {
    const search = vi.fn().mockRejectedValue(new Error('memory is down'));
    const { result } = renderSearch('x', undefined, {
      ...createMemoryGatewayMockAdapter(),
      search,
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toEqual(new Error('memory is down'));
    expect(search).toHaveBeenCalledWith('x', {});
  });
});
