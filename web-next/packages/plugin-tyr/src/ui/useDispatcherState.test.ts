import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createElement } from 'react';
import { useDispatcherState } from './useDispatcherState';
import { createMockDispatcherService } from '../adapters/mock';
import type { IDispatcherService } from '../ports';

function wrap(dispatcher: IDispatcherService) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return createElement(
      QueryClientProvider,
      { client },
      createElement(ServicesProvider, { services: { 'tyr.dispatcher': dispatcher } }, children),
    );
  };
}

describe('useDispatcherState', () => {
  it('returns dispatcher state', async () => {
    const { result } = renderHook(() => useDispatcherState(), {
      wrapper: wrap(createMockDispatcherService()),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).not.toBeNull();
    expect(result.current.data?.threshold).toBe(70);
  });

  it('exposes loading state initially', () => {
    const { result } = renderHook(() => useDispatcherState(), {
      wrapper: wrap(createMockDispatcherService()),
    });
    expect(result.current.isLoading).toBe(true);
  });

  it('exposes error when service throws', async () => {
    const failing: IDispatcherService = {
      getState: async () => {
        throw new Error('dispatcher offline');
      },
      setRunning: async () => {},
      setThreshold: async () => {},
      setAutoContinue: async () => {},
      getLog: async () => [],
    };
    const { result } = renderHook(() => useDispatcherState(), { wrapper: wrap(failing) });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
