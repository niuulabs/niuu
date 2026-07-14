import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { AgentDirectoryPage } from '../domain';
import type { IAgentDirectory } from '../ports';
import { useAgents } from './useAgents';

const emptyPage: AgentDirectoryPage = {
  items: [],
  warnings: [],
  sources: [],
  partial: false,
  revision: 'revision-a',
};

function wrap(directory: IAgentDirectory) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ 'observatory.agents': directory }}>
          {children}
        </ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('useAgents', () => {
  it('reports loading before returning a principal-scoped page', async () => {
    let resolve!: (page: AgentDirectoryPage) => void;
    const listAgents = vi.fn(() => new Promise<AgentDirectoryPage>((next) => (resolve = next)));
    const directory = { listAgents, getAgent: vi.fn() } as IAgentDirectory;
    const { result } = renderHook(() => useAgents({ skills: ['code'] }), {
      wrapper: wrap(directory),
    });

    expect(result.current.isLoading).toBe(true);
    resolve(emptyPage);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(emptyPage);
    expect(listAgents).toHaveBeenCalledWith({ skills: ['code'] });
  });

  it('surfaces directory failures', async () => {
    const directory = {
      listAgents: vi.fn().mockRejectedValue(new Error('observatory unavailable')),
      getAgent: vi.fn(),
    } as IAgentDirectory;
    const { result } = renderHook(() => useAgents(), { wrapper: wrap(directory) });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toEqual(new Error('observatory unavailable'));
  });
});
