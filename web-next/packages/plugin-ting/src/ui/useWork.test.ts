import { describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createElement, type ReactNode } from 'react';
import type { WorkCollection, WorkSummary } from '../domain/work';
import { mergeWorkPages, useWorkCollection, useWorkDetail } from './useWork';

const execution = (id: string, title = id): WorkSummary =>
  ({
    id: `execution:${id}`,
    kind: 'workflowExecution',
    title,
  }) as WorkSummary;

const page = (
  executions: WorkSummary[],
  executionNextCursor: string | null,
  source = 'page',
): WorkCollection => ({
  projects: [execution(`${source}-project`)],
  campaigns: [execution(`${source}-campaign`)],
  executions,
  executionNextCursor,
  coverage: [{ source, connectionId: null, status: 'complete', error: null }],
});

describe('mergeWorkPages', () => {
  it('returns null without a fetched page', () => {
    expect(mergeWorkPages(undefined)).toBeNull();
    expect(mergeWorkPages([])).toBeNull();
  });

  it('keeps unpaged sections from the first response and paging state from the latest', () => {
    const first = page([execution('one'), execution('shared', 'older')], 'cursor-2', 'first');
    const second = page([execution('shared', 'newer'), execution('two')], null, 'second');

    const merged = mergeWorkPages([first, second]);
    expect(merged?.projects).toBe(first.projects);
    expect(merged?.campaigns).toBe(first.campaigns);
    expect(merged?.coverage).toBe(second.coverage);
    expect(merged?.executionNextCursor).toBeNull();
    expect(merged?.executions.map((item) => [item.id, item.title])).toEqual([
      ['execution:one', 'one'],
      ['execution:shared', 'newer'],
      ['execution:two', 'two'],
    ]);
  });
});

function wrapperFor(work: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(
      QueryClientProvider,
      { client },
      createElement(ServicesProvider, { services: { 'ting.work': work } }, children),
    );
  };
}

describe('useWorkCollection', () => {
  it('asks for the first page without a cursor and the next page with it', async () => {
    const list = vi
      .fn()
      .mockResolvedValueOnce(page([execution('one')], 'cursor-2'))
      .mockResolvedValueOnce(page([execution('two')], null));
    const { result } = renderHook(() => useWorkCollection(), { wrapper: wrapperFor({ list }) });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(list).toHaveBeenNthCalledWith(1, { executionLimit: 50 });
    expect(result.current.hasNextPage).toBe(true);

    await act(async () => {
      await result.current.fetchNextPage();
    });

    expect(list).toHaveBeenNthCalledWith(2, { executionLimit: 50, executionCursor: 'cursor-2' });
    await waitFor(() => expect(result.current.hasNextPage).toBe(false));
    expect(mergeWorkPages(result.current.data?.pages)?.executions.map((item) => item.id)).toEqual([
      'execution:one',
      'execution:two',
    ]);
  });
});

describe('useWorkDetail', () => {
  it('does not fetch until both the kind and the id are known', () => {
    const get = vi.fn();
    const { result } = renderHook(() => useWorkDetail('project', null), {
      wrapper: wrapperFor({ get }),
    });

    expect(result.current.fetchStatus).toBe('idle');
    expect(get).not.toHaveBeenCalled();
  });

  it('fetches the detail of the selected item', async () => {
    const detail = { item: execution('one'), tasks: [], coverage: [] };
    const get = vi.fn().mockResolvedValue(detail);
    const { result } = renderHook(() => useWorkDetail('execution', 'one'), {
      wrapper: wrapperFor({ get }),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(get).toHaveBeenCalledWith('execution', 'one');
    expect(result.current.data).toEqual(detail);
  });
});
