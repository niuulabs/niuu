import { describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { useSessionRead } from './useSessionRead';
import { createMockVolundrService } from '../../adapters/mock';
import { SessionReadStateConflictError, type Session } from '../../domain/session';
import type { IVolundrService } from '../../ports/IVolundrService';
import type { SessionReadState } from '../../models/volundr.model';

const unreadState: SessionReadState = {
  isUnread: true,
  latestOutputSeq: 14,
  latestOutputTurnId: 'turn-9',
  latestOutputAt: '2026-11-05T10:00:00Z',
  readThroughSeq: 9,
  manuallyUnread: false,
  revision: 3,
};
const readState: SessionReadState = { ...unreadState, isUnread: false, readThroughSeq: 14 };

function session(id: string, state?: SessionReadState): Session {
  return {
    id,
    name: `session-${id}`,
    ravnId: id,
    personaName: id,
    templateId: 't',
    clusterId: 'thor',
    state: 'idle',
    startedAt: '2026-11-05T09:00:00Z',
    resources: {
      cpuRequest: 0,
      cpuLimit: 0,
      cpuUsed: 0,
      memRequestMi: 0,
      memLimitMi: 0,
      memUsedMi: 0,
      gpuCount: 0,
    },
    env: {},
    events: [],
    ...(state ? { readState: state } : {}),
  };
}

function setup(overrides: Partial<IVolundrService> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  const service = {
    ...createMockVolundrService(),
    changeSessionReadState: vi.fn(async () => readState),
    ...overrides,
  } as IVolundrService;
  const view = renderHook(() => useSessionRead(), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ volundr: service }}>{children}</ServicesProvider>
      </QueryClientProvider>
    ),
  });
  return { ...view, service, invalidate };
}

describe('useSessionRead', () => {
  it('marks read with the revision and the output the caller saw, then refreshes the lists', async () => {
    const { result, service, invalidate } = setup();
    let ok = false;
    await act(async () => {
      ok = await result.current.markRead(session('a', unreadState));
    });
    expect(ok).toBe(true);
    expect(service.changeSessionReadState).toHaveBeenCalledExactlyOnceWith('a', {
      state: 'read',
      expectedRevision: 3,
      throughSeq: 14,
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['volundr', 'domain-sessions'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['volundr', 'domain-session'] });
    expect(result.current.error).toBeNull();
  });

  it('marks unread against the same revision and output', async () => {
    const { result, service } = setup();
    await act(async () => {
      await result.current.markUnread(session('a', readState));
    });
    expect(service.changeSessionReadState).toHaveBeenCalledExactlyOnceWith('a', {
      state: 'unread',
      expectedRevision: 3,
      throughSeq: 14,
    });
  });

  it('does not write when there is nothing to change', async () => {
    const { result, service } = setup();
    await act(async () => {
      expect(await result.current.markRead(session('a', readState))).toBe(true);
      expect(
        await result.current.markUnread(session('b', { ...readState, manuallyUnread: true })),
      ).toBe(true);
    });
    expect(service.changeSessionReadState).not.toHaveBeenCalled();
  });

  it('reports a revision conflict, refreshes what won, and does not retry', async () => {
    const changeSessionReadState = vi.fn(async () => {
      throw new SessionReadStateConflictError();
    });
    const { result, invalidate } = setup({ changeSessionReadState });
    let ok = true;
    await act(async () => {
      ok = await result.current.markRead(session('a', unreadState));
    });
    expect(ok).toBe(false);
    expect(changeSessionReadState).toHaveBeenCalledTimes(1);
    expect(result.current.error).toContain('marked read or unread elsewhere');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['volundr', 'domain-sessions'] });

    act(() => result.current.clearError());
    expect(result.current.error).toBeNull();
  });

  it('shows the host answer when the change fails for another reason', async () => {
    const changeSessionReadState = vi.fn(async () => {
      throw Object.assign(new Error('Forbidden'), { detail: 'Access denied' });
    });
    const { result, invalidate } = setup({ changeSessionReadState });
    await act(async () => {
      await result.current.markUnread(session('a', readState));
    });
    expect(result.current.error).toBe('Access denied');
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('refuses a session whose host reports no read state', async () => {
    const { result, service } = setup();
    let ok = true;
    await act(async () => {
      ok = await result.current.markUnread(session('a'));
    });
    expect(ok).toBe(false);
    expect(result.current.error).toContain('did not report a read state');
    expect(service.changeSessionReadState).not.toHaveBeenCalled();
  });

  it('tracks the sessions with an action in flight', async () => {
    let release: (state: SessionReadState) => void = () => {};
    const changeSessionReadState = vi.fn(
      () => new Promise<SessionReadState>((resolve) => (release = resolve)),
    );
    const { result } = setup({ changeSessionReadState });
    let pending: Promise<boolean> = Promise.resolve(false);
    act(() => {
      pending = result.current.markRead(session('a', unreadState));
    });
    expect([...result.current.pendingIds]).toEqual(['a']);
    await act(async () => {
      release(readState);
      await pending;
    });
    expect(result.current.pendingIds.size).toBe(0);
  });

  it('marks every unread session read and says how many failed when some do', async () => {
    const changeSessionReadState = vi.fn(async (id: string) => {
      if (id === 'b') throw new Error('Host unreachable');
      return readState;
    });
    const { result } = setup({ changeSessionReadState });
    let ok = true;
    await act(async () => {
      ok = await result.current.markAllRead([
        session('a', unreadState),
        session('b', unreadState),
        session('c', readState),
        session('d'),
      ]);
    });
    expect(ok).toBe(false);
    expect(changeSessionReadState).toHaveBeenCalledTimes(2);
    expect(result.current.error).toBe('1 of 2 sessions could not be marked read: Host unreachable');

    await act(async () => {
      ok = await result.current.markAllRead([session('a', unreadState)]);
    });
    expect(ok).toBe(true);
    expect(result.current.error).toBeNull();
  });
});
