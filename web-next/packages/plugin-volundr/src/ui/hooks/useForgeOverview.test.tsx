import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type { VolundrStats } from '../../models/volundr.model';
import type { Cluster } from '../../domain/cluster';
import type { SessionReadOptions } from '../../ports/IVolundrService';
import { useForgeOverview } from './useForgeOverview';

const stats = (n: number): VolundrStats => ({
  activeSessions: n,
  totalSessions: n,
  sessionsToday: n,
  tokensToday: n,
  localTokens: n,
  cloudTokens: n,
  costToday: n,
});
const store = { listSources: vi.fn(), listRequestTimeoutMs: 1000 };
const service = { getStats: vi.fn() };
const adapter = { getClusters: vi.fn() };
vi.mock('@niuulabs/plugin-sdk', () => ({
  useService: (key: string) =>
    ({ sessionStore: store, volundr: service, clusterAdapter: adapter })[key],
}));
function mount() {
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return {
    ...renderHook(() => useForgeOverview(), {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={cache}>{children}</QueryClientProvider>
      ),
    }),
    cache,
  };
}
beforeEach(() => {
  vi.resetAllMocks();
  store.listRequestTimeoutMs = 1000;
  store.listSources = vi.fn(async () => [
    { id: 'thor', name: 'Thor' },
    { id: 'remote', name: 'Remote' },
  ]);
  service.getStats.mockResolvedValue(stats(1));
  adapter.getClusters.mockImplementation(async ({ instanceId }: SessionReadOptions) => [
    { id: instanceId } as Cluster,
  ]);
});

it('shows healthy metrics and resources independently while another host is stuck', async () => {
  let resolveRemote!: (value: VolundrStats) => void;
  const pending = new Promise<VolundrStats>((resolve) => {
    resolveRemote = resolve;
  });
  service.getStats.mockImplementation(async ({ instanceId }: SessionReadOptions) =>
    instanceId === 'remote'
      ? pending
      : { ...stats(2), sparklines: { tokensToday: [1, 2], activePods: [3] } },
  );
  adapter.getClusters.mockImplementation(async ({ instanceId }: SessionReadOptions) => {
    if (instanceId === 'remote') throw new Error('Offline');
    return [{ id: instanceId } as Cluster];
  });
  const { result } = mount();
  await waitFor(() => expect(result.current.stats?.tokensToday).toBe(2));
  expect(result.current.clusters).toEqual([{ id: 'thor' }]);
  expect(result.current.states.find((s) => s.id === 'metrics:remote')?.loading).toBe(true);
  expect(result.current.states.find((s) => s.id === 'resources:remote')?.error).toBe('Offline');
  await act(async () => {
    resolveRemote({ ...stats(4), sparklines: { tokensToday: [4, 5, 6], costToday: [2] } });
  });
  await waitFor(() => expect(result.current.stats?.tokensToday).toBe(6));
  expect(result.current.stats).toMatchObject({
    ...stats(6),
    sparklines: { tokensToday: [5, 7, 6], activePods: [3], costToday: [2] },
  });
  expect(
    service.getStats.mock.calls.every(
      ([options]) => options.instanceId && options.signal instanceof AbortSignal,
    ),
  ).toBe(true);
});

it('preserves stale values visibly, recovers on retry, and forgets deregistered hosts', async () => {
  const { result, cache } = mount();
  await waitFor(() => expect(result.current.stats?.activeSessions).toBe(2));
  service.getStats.mockRejectedValue(new Error('Unavailable'));
  await act(async () => {
    await result.current.refetch();
  });
  await waitFor(() => expect(result.current.states[0]?.stale).toBe(true));
  expect(result.current.stats?.activeSessions).toBe(2);
  service.getStats.mockResolvedValue(stats(3));
  await act(async () => {
    await result.current.refetch();
  });
  await waitFor(() => expect(result.current.stats?.activeSessions).toBe(6));
  store.listSources.mockResolvedValue([{ id: 'thor', name: 'Thor' }]);
  await act(async () => {
    await cache.invalidateQueries({ queryKey: ['volundr', 'targets'] });
  });
  await waitFor(() => expect(result.current.stats?.activeSessions).toBe(3));
  expect(result.current.clusters).toEqual([{ id: 'thor' }]);
});

it('aborts stuck metrics on deadline and reports errors without inventing zero totals', async () => {
  store.listRequestTimeoutMs = 20;
  const signals: AbortSignal[] = [];
  service.getStats.mockImplementation(
    ({ signal }: SessionReadOptions) =>
      new Promise((_resolve, reject) => {
        signals.push(signal!);
        signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {
          once: true,
        });
      }),
  );
  const { result } = mount();
  await waitFor(() =>
    expect(result.current.states.find((s) => s.id === 'metrics:thor')?.error).toContain(
      'Connection timed out',
    ),
  );
  expect(signals.every((signal) => signal.aborted)).toBe(true);
  expect(result.current.stats).toBeUndefined();
  expect(result.current.clusters).toHaveLength(2);
});

it('cancels host requests on leaving the overview', async () => {
  const signals: AbortSignal[] = [];
  service.getStats.mockImplementation(
    ({ signal }: SessionReadOptions) =>
      new Promise((_resolve, reject) => {
        signals.push(signal!);
        signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {
          once: true,
        });
      }),
  );
  const { unmount } = mount();
  await waitFor(() => expect(signals).toHaveLength(2));
  unmount();
  expect(signals.every((signal) => signal.aborted)).toBe(true);
});

it('exposes registry errors without fetching aggregate metrics', async () => {
  store.listSources.mockRejectedValue(new Error('Denied'));
  const { result } = mount();
  await waitFor(() => expect(result.current.states[0]?.error).toBe('Denied'));
  expect(service.getStats).not.toHaveBeenCalled();
  expect(adapter.getClusters).not.toHaveBeenCalled();
});

it('handles an empty registry without probing any host', async () => {
  store.listSources.mockResolvedValue([]);
  const { result } = mount();
  await waitFor(() => expect(result.current.stats).toEqual(stats(0)));
  expect(result.current.states).toEqual([]);
  expect(service.getStats).not.toHaveBeenCalled();
});
