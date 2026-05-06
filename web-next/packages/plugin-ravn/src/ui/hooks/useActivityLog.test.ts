import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createElement } from 'react';
import { useActivityLog } from './useActivityLog';
import { createMockSessionStream, createMockTriggerStore } from '../../adapters/mock';
import type { Session } from '../../domain/session';
import type { Trigger } from '../../domain/trigger';

function makeWrapper(
  sessionStream = createMockSessionStream(),
  triggerStore = createMockTriggerStore(),
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const services = {
    'ravn.sessions': sessionStream,
    'ravn.triggers': triggerStore,
  };
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return createElement(
      QueryClientProvider,
      { client },
      createElement(ServicesProvider, { services }, children),
    );
  };
}

describe('useActivityLog', () => {
  it('returns undefined while sessions are loading', () => {
    const slow = {
      listSessions: () => new Promise<Session[]>(() => undefined),
      getSession: (_id: string) => new Promise<Session>(() => undefined),
      getMessages: () => Promise.resolve([]),
    };
    const { result } = renderHook(() => useActivityLog(), {
      wrapper: makeWrapper(slow),
    });
    expect(result.current.isLoading).toBe(true);
    expect(result.current.data).toBeUndefined();
  });

  it('returns populated entries after loading', async () => {
    const { result } = renderHook(() => useActivityLog(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data).toBeDefined();
    expect(result.current.data!.length).toBeGreaterThan(0);
  });

  it('caps at 9 entries', async () => {
    const { result } = renderHook(() => useActivityLog(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data!.length).toBeLessThanOrEqual(9);
  });

  it('entries are sorted by timestamp descending', async () => {
    const { result } = renderHook(() => useActivityLog(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    const entries = result.current.data!;
    for (let i = 1; i < entries.length; i++) {
      expect(entries[i - 1]!.ts >= entries[i]!.ts).toBe(true);
    }
  });

  it('includes session kind entries', async () => {
    const { result } = renderHook(() => useActivityLog(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    const kinds = result.current.data!.map((e) => e.kind);
    expect(kinds).toContain('session');
  });

  it('includes trigger kind entries when triggers are recent enough', async () => {
    const { result } = renderHook(() => useActivityLog(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    const kinds = result.current.data!.map((e) => e.kind);
    expect(kinds).toContain('trigger');
  });

  it('includes emit entries for completed or stopped sessions', async () => {
    const stoppedSession: Session = {
      id: 'feed0001-0000-4000-8000-000000000001',
      ravnId: 'feed0001-0000-4000-8000-000000000002',
      personaName: 'reviewer',
      personaRole: 'review',
      personaLetter: 'R',
      status: 'stopped',
      model: 'claude-4-sonnet',
      createdAt: '2026-01-15T08:55:00Z',
      title: 'Finalize security verdict',
      messageCount: 4,
      tokenCount: 1200,
      costUsd: 0.03,
    };
    const customSessions = {
      listSessions: () => Promise.resolve([stoppedSession]),
      getSession: (_id: string) => Promise.resolve(stoppedSession),
      getMessages: () => Promise.resolve([]),
    };
    const { result } = renderHook(() => useActivityLog(), {
      wrapper: makeWrapper(customSessions),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    const kinds = result.current.data!.map((e) => e.kind);
    expect(kinds).toContain('emit');
  });

  it('returns isError true when sessions fail', async () => {
    const failing = {
      listSessions: () => Promise.reject(new Error('fleet offline')),
      getSession: (_id: string) => Promise.reject(new Error('fleet offline')),
      getMessages: () => Promise.resolve([]),
    };
    const { result } = renderHook(() => useActivityLog(), {
      wrapper: makeWrapper(failing),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isError).toBe(true);
    expect(result.current.data).toBeUndefined();
  });

  it('returns empty array when sessions list is empty', async () => {
    const empty = {
      listSessions: () => Promise.resolve([] as Session[]),
      getSession: (_id: string) => Promise.resolve({} as Session),
      getMessages: () => Promise.resolve([]),
    };
    const noTriggers = {
      listTriggers: () => Promise.resolve([] as Trigger[]),
      createTrigger: async (t: Omit<Trigger, 'id' | 'createdAt'>) => ({
        ...t,
        id: 'x',
        createdAt: '',
      }),
      deleteTrigger: async () => undefined,
    };
    const { result } = renderHook(() => useActivityLog(), {
      wrapper: makeWrapper(empty, noTriggers),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data).toEqual([]);
  });
});
