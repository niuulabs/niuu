import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type { ISessionStore } from '../../ports/ISessionStore';
import type { Session } from '../../domain/session';
import { useSessionDetail, useSessionList } from './useSessionStore';

let store: ISessionStore;
vi.mock('@niuulabs/plugin-sdk', () => ({ useService: () => store }));
const row = (id: string) => ({ id, name: id }) as Session;
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function mount() {
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return {
    ...renderHook(() => useSessionList(), {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={cache}>{children}</QueryClientProvider>
      ),
    }),
    cache,
  };
}
beforeEach(() => {
  store = {
    listSources: vi.fn(async () => [
      { id: 'thor', name: 'Thor' },
      { id: 'remote', name: 'Remote' },
    ]),
    listSessions: vi.fn(async () => []),
  } as unknown as ISessionStore;
});

it('shows healthy sessions while another host and archives are still loading, then reports partial failure', async () => {
  const remote = deferred<Session[]>(),
    archive = deferred<Session[]>();
  store.listSessions = vi.fn(async (filters) =>
    filters?.instanceId === 'remote'
      ? remote.promise
      : filters?.archivedOnly
        ? archive.promise
        : [row('ready')],
  );
  const { result } = mount();
  await waitFor(() => expect(result.current.data).toEqual([row('ready')]));
  expect(result.current.isLoading).toBe(false);
  expect(result.current.sources.filter((source) => source.loading)).toHaveLength(3);
  await act(async () => {
    archive.resolve([row('old')]);
    remote.reject(new Error('Forbidden'));
  });
  await waitFor(() =>
    expect(result.current.sources.filter((source) => source.error)).toHaveLength(2),
  );
  expect(result.current.data).toEqual([row('ready'), row('old')]);
  expect(result.current.isError).toBe(false);
  expect(store.listSessions).toHaveBeenCalledTimes(4);
});

it('keeps initial loading until the first active inventory arrives, even if an empty archive is ready', async () => {
  store.listSources = async () => [{ id: 'thor', name: 'Thor' }];
  const active = deferred<Session[]>();
  store.listSessions = async (filters) => (filters?.archivedOnly ? [] : active.promise);
  const { result } = mount();
  await waitFor(() => expect(result.current.sources).toHaveLength(2));
  expect(result.current.isLoading).toBe(true);
  await act(async () => active.resolve([]));
  await waitFor(() => expect(result.current.isLoading).toBe(false));
  expect(result.current.data).toEqual([]);
});

it('marks failed refreshes stale, retries them, and removes deregistered host results', async () => {
  let failing = false;
  store.listSessions = async (filters) => {
    if (failing && filters?.instanceId === 'remote') throw new Error('Offline');
    return filters?.archivedOnly ? [] : [row(filters!.instanceId!)];
  };
  const { result, cache } = mount();
  await waitFor(() => expect(result.current.data).toHaveLength(2));
  failing = true;
  await act(async () => {
    await cache.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] });
  });
  await waitFor(() =>
    expect(result.current.sources.find((source) => source.id === 'remote')?.stale).toBe(true),
  );
  expect(result.current.data).toHaveLength(2);
  failing = false;
  await act(async () => {
    await result.current.refetch();
  });
  await waitFor(() => expect(result.current.sources.every((source) => !source.error)).toBe(true));
  store.listSources = async () => [{ id: 'thor', name: 'Thor' }];
  await act(async () => {
    await result.current.refetch();
  });
  await waitFor(() => expect(result.current.data).toEqual([row('thor')]));
});

it('bounds a stuck source with a real abort and cancels remaining requests on unmount', async () => {
  store = { ...store, listRequestTimeoutMs: 30 };
  const signals: AbortSignal[] = [];
  store.listSessions = async (_filters, signal) => {
    signals.push(signal!);
    return new Promise((_resolve, reject) =>
      signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {
        once: true,
      }),
    );
  };
  const { result, unmount } = mount();
  await waitFor(() => expect(result.current.sources[0]?.error).toContain('Connection timed out'));
  expect(signals.every((signal) => signal.aborted)).toBe(true);
  expect(result.current.isError).toBe(true);
  unmount();
  expect(result.current.data).toBeUndefined();
});

it('aborts reads when the last subscriber leaves', async () => {
  const signals: AbortSignal[] = [];
  store.listSessions = async (_filters, signal) => {
    signals.push(signal!);
    return new Promise((_resolve, reject) =>
      signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {
        once: true,
      }),
    );
  };
  const { unmount } = mount();
  await waitFor(() => expect(signals).toHaveLength(4));
  unmount();
  expect(signals.every((signal) => signal.aborted)).toBe(true);
});

it('does not mask discovery errors or replace them with an aggregate fetch', async () => {
  store.listSources = async () => {
    throw new Error('Registry denied');
  };
  const { result } = mount();
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(result.current.error?.message).toBe('Registry denied');
  expect(store.listSessions).not.toHaveBeenCalled();
});

it('keeps available sessions but exposes registry refresh failures instead of silently using cached hosts', async () => {
  store.listSessions = async (filters) =>
    filters?.archivedOnly ? [] : [row(filters!.instanceId!)];
  const { result } = mount();
  await waitFor(() => expect(result.current.data).toHaveLength(2));
  store.listSources = async () => {
    throw new Error('Registry denied');
  };
  await act(async () => {
    await result.current.refetch();
  });
  await waitFor(() =>
    expect(result.current.sources[0]).toMatchObject({
      name: 'Forge registry',
      error: 'Registry denied',
      stale: true,
    }),
  );
  expect(result.current.data).toHaveLength(2);
  expect(result.current.isError).toBe(false);
});

it('handles an empty registry without probing arbitrary hosts', async () => {
  store.listSources = async () => [];
  const { result } = mount();
  await waitFor(() => expect(result.current.data).toEqual([]));
  expect(result.current.isLoading).toBe(false);
  expect(store.listSessions).not.toHaveBeenCalled();
});

it('keeps same-id session details source-qualified and forwards cancellation', async () => {
  store.getSession = vi.fn(async () => row('shared-id'));
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => useSessionDetail('shared-id', 'build'), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={cache}>{children}</QueryClientProvider>
    ),
  });

  await waitFor(() => expect(result.current.data).toEqual(row('shared-id')));
  expect(store.getSession).toHaveBeenCalledWith('shared-id', {
    instanceId: 'build',
    signal: expect.any(AbortSignal),
  });
  expect(cache.getQueryData(['volundr', 'domain-session', 'shared-id', 'build'])).toEqual(
    row('shared-id'),
  );
  expect(cache.getQueryData(['volundr', 'domain-session', 'shared-id', 'thor'])).toBeUndefined();
});

it('prefers live data over an older cached archived copy of a restored session', async () => {
  store.listSources = async () => [{ id: 'thor', name: 'Thor' }];
  store.listSessions = async (filters) => [
    { ...row('same'), state: filters?.archivedOnly ? 'archived' : 'running' },
  ];
  const { result } = mount();
  await waitFor(() => expect(result.current.data?.[0]?.state).toBe('running'));
  expect(result.current.data).toHaveLength(1);
});

it('retains single-source store support with cancellation and explicit refresh', async () => {
  delete store.listSources;
  store.listSessions = vi.fn(async () => [row('single')]);
  const { result } = mount();
  await waitFor(() => expect(result.current.data).toEqual([row('single')]));
  expect(result.current.sources).toEqual([]);
  await act(async () => {
    await result.current.refetch();
  });
  expect(store.listSessions).toHaveBeenCalledTimes(2);
});
