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

  it('formats every session role and trigger kind fallback', async () => {
    const makeSession = (
      index: number,
      personaRole: Session['personaRole'],
      overrides: Partial<Session> = {},
    ): Session => ({
      id: `7000000${index}-0000-4000-8000-000000000001`,
      ravnId: `7000000${index}-0000-4000-8000-000000000002`,
      personaName: `persona-${index}`,
      personaRole,
      personaLetter: 'R',
      status: 'stopped',
      model: 'test-model',
      createdAt: `2026-07-11T12:${String(index).padStart(2, '0')}:00Z`,
      tokenCount: 0,
      costUsd: 0,
      ...overrides,
    });
    const sessions = [
      makeSession(1, 'review', { title: 'Review' }),
      makeSession(2, 'observe', { title: 'Observe' }),
      makeSession(3, 'knowledge', { title: 'Knowledge' }),
      makeSession(4, 'qa', { title: 'QA' }),
      makeSession(5, 'build', { title: 'Build' }),
      makeSession(6, 'report', { title: 'Report' }),
      makeSession(7, 'coord', { title: 'Coordinate' }),
      makeSession(8, 'investigate', { title: 'Investigate' }),
      makeSession(9, undefined, { status: 'running', messageCount: undefined }),
    ];
    const triggers: Trigger[] = [
      {
        id: 'trigger-cron',
        kind: 'cron',
        personaName: 'cron',
        spec: '* * * * *',
        enabled: true,
        createdAt: '2026-07-11T13:00:00Z',
      },
      {
        id: 'trigger-event',
        kind: 'event',
        personaName: 'event',
        spec: 'code.changed',
        enabled: true,
        createdAt: '2026-07-11T13:01:00Z',
        lastFiredAt: '2026-07-11T13:05:00Z',
      },
      {
        id: 'trigger-manual',
        kind: 'manual',
        personaName: 'manual',
        spec: 'run-now',
        enabled: true,
        createdAt: '2026-07-11T13:02:00Z',
      },
      {
        id: 'trigger-webhook',
        kind: 'webhook',
        personaName: 'webhook',
        spec: '/hooks/run',
        enabled: true,
        createdAt: '2026-07-11T13:03:00Z',
      },
      {
        id: 'trigger-fallback',
        kind: 'custom' as never,
        personaName: 'custom',
        spec: 'fallback',
        enabled: true,
        createdAt: '2026-07-11T13:04:00Z',
      },
    ];
    const sessionStream = {
      listSessions: async () => sessions,
      getSession: async () => sessions[0]!,
      getMessages: async () => [],
    };
    const triggerStore = {
      listTriggers: async () => triggers,
      createTrigger: async () => triggers[0]!,
      deleteTrigger: async () => undefined,
    };

    const { result } = renderHook(() => useActivityLog(), {
      wrapper: makeWrapper(sessionStream, triggerStore),
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data).toHaveLength(9);
  });

  it('reflects trigger-only loading and error states', async () => {
    const sessionStream = {
      listSessions: async () => [] as Session[],
      getSession: async () => ({}) as Session,
      getMessages: async () => [],
    };
    const slowTriggers = {
      listTriggers: () => new Promise<Trigger[]>(() => undefined),
      createTrigger: async () => ({}) as Trigger,
      deleteTrigger: async () => undefined,
    };
    const loading = renderHook(() => useActivityLog(), {
      wrapper: makeWrapper(sessionStream, slowTriggers),
    });
    await waitFor(() => expect(loading.result.current.data).toEqual([]));
    expect(loading.result.current.isLoading).toBe(true);

    const failedTriggers = {
      ...slowTriggers,
      listTriggers: async () => {
        throw new Error('triggers unavailable');
      },
    };
    const failed = renderHook(() => useActivityLog(), {
      wrapper: makeWrapper(sessionStream, failedTriggers),
    });
    await waitFor(() => expect(failed.result.current.isError).toBe(true));
    expect(failed.result.current.data).toEqual([]);
  });
});
