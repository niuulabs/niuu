import { describe, it, expect, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createElement, type ReactNode } from 'react';
import { useSessions, useSession, useMessages } from './useSessions';
import type { Session } from '../../domain/session';
import type { Message } from '../../domain/message';

const SAMPLE_SESSION: Session = {
  id: '10000001-0000-4000-8000-000000000001',
  ravnId: 'a3f1b2c4-8e7d-4a6f-9b0c-1d2e3f4a5b6c',
  personaName: 'coder',
  status: 'running',
  model: 'claude-sonnet-4-6',
  createdAt: '2026-04-15T09:00:00Z',
};

const SAMPLE_MESSAGE: Message = {
  id: '00000001-0000-4000-8000-000000000001',
  sessionId: '10000001-0000-4000-8000-000000000001',
  kind: 'user',
  content: 'Hello',
  ts: '2026-04-15T09:00:01Z',
};

function makeWrapper(services: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(
      QueryClientProvider,
      { client },
      createElement(ServicesProvider, { services }, children),
    );
  };
}

describe('useSessions', () => {
  it('returns session list', async () => {
    const svc = {
      listSessions: vi.fn().mockResolvedValue([SAMPLE_SESSION]),
      getSession: vi.fn(),
      getMessages: vi.fn(),
    };
    const { result } = renderHook(() => useSessions(), {
      wrapper: makeWrapper({ 'ravn.sessions': svc }),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
  });

  it('enters error state when service rejects', async () => {
    const svc = {
      listSessions: vi.fn().mockRejectedValue(new Error('timeout')),
      getSession: vi.fn(),
      getMessages: vi.fn(),
    };
    const { result } = renderHook(() => useSessions(), {
      wrapper: makeWrapper({ 'ravn.sessions': svc }),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it('polls until an idle resident receives its chat endpoint', async () => {
    vi.useFakeTimers();
    const ready = {
      ...SAMPLE_SESSION,
      status: 'running' as const,
      chatEndpoint: 'wss://sessions.example.test/session',
    };
    let resolveReady!: (sessions: Session[]) => void;
    const svc = {
      listSessions: vi
        .fn()
        .mockResolvedValueOnce([{ ...SAMPLE_SESSION, status: 'idle', chatEndpoint: null }])
        .mockImplementationOnce(
          () =>
            new Promise<Session[]>((resolve) => {
              resolveReady = resolve;
            }),
        ),
      getSession: vi.fn(),
      getMessages: vi.fn(),
    };

    try {
      const { result } = renderHook(() => useSessions(), {
        wrapper: makeWrapper({ 'ravn.sessions': svc }),
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(result.current.data?.[0]?.status).toBe('idle');

      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_000);
      });
      await act(async () => {
        resolveReady([ready]);
        await vi.advanceTimersByTimeAsync(0);
      });

      expect(svc.listSessions).toHaveBeenCalledTimes(2);
      expect(result.current.data?.[0]?.chatEndpoint).toBe(ready.chatEndpoint);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('useSession', () => {
  it('fetches a single session', async () => {
    const svc = {
      listSessions: vi.fn(),
      getSession: vi.fn().mockResolvedValue(SAMPLE_SESSION),
      getMessages: vi.fn(),
    };
    const { result } = renderHook(() => useSession('10000001-0000-4000-8000-000000000001'), {
      wrapper: makeWrapper({ 'ravn.sessions': svc }),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.personaName).toBe('coder');
  });

  it('is disabled when id is empty', () => {
    const svc = { listSessions: vi.fn(), getSession: vi.fn(), getMessages: vi.fn() };
    const { result } = renderHook(() => useSession(''), {
      wrapper: makeWrapper({ 'ravn.sessions': svc }),
    });

    expect(result.current.fetchStatus).toBe('idle');
    expect(svc.getSession).not.toHaveBeenCalled();
  });
});

describe('useMessages', () => {
  it('fetches messages for a session', async () => {
    const svc = {
      listSessions: vi.fn(),
      getSession: vi.fn(),
      getMessages: vi.fn().mockResolvedValue([SAMPLE_MESSAGE]),
    };
    const { result } = renderHook(() => useMessages('10000001-0000-4000-8000-000000000001'), {
      wrapper: makeWrapper({ 'ravn.sessions': svc }),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(1);
    expect(result.current.data![0]!.kind).toBe('user');
  });

  it('is disabled when sessionId is empty', () => {
    const svc = { listSessions: vi.fn(), getSession: vi.fn(), getMessages: vi.fn() };
    const { result } = renderHook(() => useMessages(''), {
      wrapper: makeWrapper({ 'ravn.sessions': svc }),
    });

    expect(result.current.fetchStatus).toBe('idle');
  });
});
