import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useSkuldChat } from './useSkuldChat';
vi.mock('@niuulabs/query', () => ({ getAuthHeaders: () => new Headers() }));
let handlers: { onOpen: () => void; onMessage: (raw: string) => void };
let socketUrl: string | null;
const send = vi.fn();
vi.mock('./useWebSocket', () => ({
  useWebSocket: (url: string | null, h: typeof handlers) => {
    socketUrl = url;
    handlers = h;
    return { sendJson: send };
  },
}));
const url = 'ws://thor.test/s/review/session';
const turn = (n: number) => ({
  id: `row-${n}`,
  role: 'assistant',
  content: `Message ${n}`,
  created_at: '2026-09-18T00:00:00Z',
});
const emit = (event: unknown) => act(() => handlers.onMessage(JSON.stringify(event)));
function mockHistory(count = 120) {
  const rows = Array.from({ length: count }, (_, n) => turn(n));
  const fetcher = vi.fn(async (raw: string, _options?: RequestInit) => {
    const u = new URL(raw);
    const end = Number(u.searchParams.get('cursor') ?? rows.length);
    const start = Math.max(0, end - Number(u.searchParams.get('limit')));
    return Response.json({
      turns: rows.slice(start, end),
      total_turns: rows.length,
      window_offset: start,
      window_end: end,
      history_protocol: 2,
      older_cursor: start ? String(start) : null,
      projection_revision: 'same',
    });
  });
  vi.stubGlobal('fetch', fetcher);
  return { fetcher, rows };
}
beforeEach(() => {
  sessionStorage.clear();
  send.mockClear();
});
describe('paged session history', () => {
  it('cancels the pre-attachment initial GET instead of finishing and downloading the batch twice', async () => {
    const { fetcher } = mockHistory(2);
    let release!: (response: Response) => void;
    let initialSignal: AbortSignal | undefined;
    fetcher.mockImplementationOnce((_, options) => {
      initialSignal = options?.signal as AbortSignal;
      return new Promise((resolve) => {
        release = resolve;
      });
    });
    const { result } = renderHook(() => useSkuldChat(url));
    act(() => handlers.onOpen());
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(initialSignal?.aborted).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(async () => release(Response.json({ turns: [turn(999)] })));
    expect(result.current.messages.map((row) => row.id)).toEqual(['row-0', 'row-1']);
  });
  it('starts with 50 and deduplicates simultaneous older loads', async () => {
    const { fetcher } = mockHistory();
    const { result } = renderHook(() => useSkuldChat(url));
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(result.current.messages).toHaveLength(50);
    expect(socketUrl).toContain('history_delivery=none');
    await act(async () => {
      await Promise.all([result.current.loadOlderHistory(), result.current.loadOlderHistory()]);
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.messages).toHaveLength(100);
    await act(async () => {
      await result.current.loadOlderHistory();
    });
    expect(result.current.messages).toHaveLength(120);
    expect(result.current.hasOlderHistory).toBe(false);
    expect(send).not.toHaveBeenCalled();
  });
  it('keeps loaded older pages on reconnect and updates the recent tail', async () => {
    const { rows } = mockHistory();
    const { result } = renderHook(() => useSkuldChat(url));
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    await act(async () => handlers.onOpen());
    await act(async () => {
      await result.current.loadOlderHistory();
    });
    rows.push(turn(120));
    act(() => handlers.onOpen());
    await waitFor(() => expect(result.current.messages.at(-1)?.id).toBe('row-120'));
    expect(result.current.messages[0]?.id).toBe('row-20');
    expect(result.current.messages).toHaveLength(101);
  });
  it('treats recovery controls as reads and preserves the live stream', async () => {
    const { fetcher } = mockHistory(2);
    const { result } = renderHook(() => useSkuldChat(url));
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    let release!: (response: Response) => void;
    fetcher.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    emit({
      type: 'error',
      code: 'conversation_history_too_large',
      request_id: 'not-a-send',
      content: 'Reload history through REST.',
    });
    emit({ type: 'assistant', message: { content: [{ type: 'text', text: 'Still working' }] } });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    await act(async () => release(Response.json({ turns: [turn(0), turn(1)] })));
    expect(result.current.messages.at(-1)?.content).toBe('Still working');
    expect(result.current.messages.at(-1)?.status).toBe('running');
    expect(result.current.messages.some((message) => message.status === 'error')).toBe(false);
    expect(send).not.toHaveBeenCalled();
  });
  it('queues one recovery during initial loading and ignores a legacy recent socket snapshot', async () => {
    const { fetcher } = mockHistory(2);
    let release!: (response: Response) => void;
    fetcher.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const { result } = renderHook(() => useSkuldChat(url));
    emit({ type: 'conversation_history', turns: [turn(999)] });
    emit({ type: 'history_gap' });
    emit({ type: 'history_gap' });
    await act(async () => release(Response.json({ turns: [turn(0), turn(1)] })));
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(result.current.messages.map((message) => message.id)).toEqual(['row-0', 'row-1']);
    expect(send).not.toHaveBeenCalled();
  });
  it('keeps failed older reads retryable and does not discard already loaded history', async () => {
    const { fetcher } = mockHistory();
    const { result } = renderHook(() => useSkuldChat(url));
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    fetcher.mockResolvedValueOnce(new Response(null, { status: 503 }));
    await act(async () => {
      await result.current.loadOlderHistory();
    });
    expect(result.current.messages).toHaveLength(50);
    expect(result.current.olderHistoryError).toContain('503');
    await act(async () => {
      await result.current.loadOlderHistory();
    });
    expect(result.current.messages).toHaveLength(100);
    expect(result.current.olderHistoryError).toBeNull();
  });
  it('recovers an invalid cursor from recent history without resending input', async () => {
    const { fetcher } = mockHistory();
    const { result } = renderHook(() => useSkuldChat(url));
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    fetcher.mockResolvedValueOnce(
      Response.json({ detail: { code: 'history_cursor_invalid' } }, { status: 409 }),
    );
    await act(async () => {
      await result.current.loadOlderHistory();
    });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
    expect(result.current.messages).toHaveLength(50);
    expect(send).not.toHaveBeenCalled();
  });
  it('cancels older reads and rejects late results from a previous session', async () => {
    const { fetcher } = mockHistory();
    const { result, rerender } = renderHook(({ endpoint }) => useSkuldChat(endpoint), {
      initialProps: { endpoint: url },
    });
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    let release!: (response: Response) => void;
    let aborted: AbortSignal | undefined;
    fetcher.mockImplementationOnce((_, options) => {
      aborted = options?.signal as AbortSignal;
      return new Promise((resolve) => {
        release = resolve;
      });
    });
    act(() => {
      void result.current.loadOlderHistory();
    });
    rerender({ endpoint: 'ws://thor.test/s/new/session' });
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(aborted?.aborted).toBe(true);
    await act(async () => release(Response.json({ turns: [turn(999)] })));
    expect(result.current.messages.some((message) => message.id === 'row-999')).toBe(false);
  });
});
