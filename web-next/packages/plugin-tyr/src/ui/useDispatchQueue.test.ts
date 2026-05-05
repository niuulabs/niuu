import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createElement } from 'react';
import { useDispatchQueue } from './useDispatchQueue';
import { createMockDispatchBus } from '../adapters/mock';
import type { IDispatchBus, DispatchQueueItem } from '../ports';

function makeQueueItem(overrides: Partial<DispatchQueueItem> = {}): DispatchQueueItem {
  return {
    sagaId: 'saga-1',
    sagaName: 'Test Saga',
    sagaSlug: 'test-saga',
    repos: ['niuulabs/volundr'],
    featureBranch: 'feat/test',
    phaseName: 'Phase 1',
    issueId: 'issue-1',
    identifier: 'NIU-123',
    title: 'Test Raid',
    description: 'Do the thing',
    status: 'todo',
    priority: 0,
    priorityLabel: '',
    estimate: 2,
    url: 'https://linear.app/issue/NIU-123',
    ...overrides,
  };
}

function wrap(dispatch: IDispatchBus) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return createElement(
      QueryClientProvider,
      { client },
      createElement(ServicesProvider, { services: { 'tyr.dispatch': dispatch } }, children),
    );
  };
}

describe('useDispatchQueue', () => {
  it('returns entries from the dispatcher queue', async () => {
    const dispatch: IDispatchBus = {
      ...createMockDispatchBus(),
      getQueue: async () => [
        makeQueueItem({ issueId: 'issue-1', title: 'Raid 1' }),
        makeQueueItem({ issueId: 'issue-2', identifier: 'NIU-124', title: 'Raid 2' }),
      ],
    };

    const { result } = renderHook(() => useDispatchQueue(), { wrapper: wrap(dispatch) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toHaveLength(2);
    expect(result.current.data?.[0]?.raid.id).toBe('issue-1');
    expect(result.current.data?.[0]?.raid.name).toBe('Raid 1');
    expect(result.current.data?.[0]?.queueItem.identifier).toBe('NIU-123');
    expect(result.current.data?.[0]?.allPhases).toHaveLength(1);
  });

  it('maps queue metadata onto saga and phase display fields', async () => {
    const dispatch: IDispatchBus = {
      ...createMockDispatchBus(),
      getQueue: async () => [
        makeQueueItem({
          sagaId: 'saga-42',
          sagaName: 'Checkout Refresh',
          sagaSlug: 'checkout-refresh',
          featureBranch: 'feat/checkout-refresh',
          phaseName: 'Payments',
        }),
      ],
    };

    const { result } = renderHook(() => useDispatchQueue(), { wrapper: wrap(dispatch) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.[0]?.saga.name).toBe('Checkout Refresh');
    expect(result.current.data?.[0]?.saga.featureBranch).toBe('feat/checkout-refresh');
    expect(result.current.data?.[0]?.phase.name).toBe('Payments');
  });

  it('returns loading state initially', () => {
    const { result } = renderHook(() => useDispatchQueue(), {
      wrapper: wrap(createMockDispatchBus()),
    });
    expect(result.current.isLoading).toBe(true);
  });

  it('returns an empty array when the dispatcher queue is empty', async () => {
    const { result } = renderHook(() => useDispatchQueue(), {
      wrapper: wrap(createMockDispatchBus()),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(0);
  });
});
