import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createElement } from 'react';
import { useWorkflowRegistryMounts } from './useWorkflowRegistryMounts';

function wrap(service: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return createElement(
      QueryClientProvider,
      { client },
      createElement(ServicesProvider, { services: service }, children),
    );
  };
}

describe('useWorkflowRegistryMounts', () => {
  it('returns an empty array when the mimir service does not expose mount listing', async () => {
    const { result } = renderHook(() => useWorkflowRegistryMounts(), {
      wrapper: wrap({ mimir: { mounts: {} } }),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([]);
  });
});
