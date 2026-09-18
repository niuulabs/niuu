import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  conversationUrl,
  fetchHistoryBatch,
  fetchHistoryItem,
  HISTORY_PAGE_BYTES,
  historySocketUrl,
  isHistoryRecovery,
} from './historyPaging';
vi.mock('@niuulabs/query', () => ({ getAuthHeaders: () => new Headers() }));
const socket = 'wss://thor.test/forge-host/build/s/session/session';
const turn = (n: number) => ({
  id: `row-${n}`,
  role: 'assistant',
  content: `Message ${n}`,
  created_at: '2026-09-18T00:00:00Z',
});
const rows = Array.from({ length: 123 }, (_, n) => turn(n));
const signal = () => new AbortController().signal;
function serve(version = 2, perPage = 15) {
  const calls: URL[] = [];
  const fetcher = vi.fn(async (raw: string) => {
    const u = new URL(raw);
    calls.push(u);
    const limit = Math.min(Number(u.searchParams.get('limit')), perPage);
    const end =
      version === 2
        ? Number(u.searchParams.get('cursor') ?? rows.length)
        : rows.length - Number(u.searchParams.get('before') ?? 0);
    const start = Math.max(0, end - limit);
    return Response.json({
      turns: rows.slice(start, end),
      total_turns: rows.length,
      window_offset: start,
      projection_revision: 'same',
      ...(version === 2
        ? { history_protocol: 2, window_end: end, older_cursor: start ? String(start) : null }
        : {}),
    });
  });
  vi.stubGlobal('fetch', fetcher);
  return { calls, fetcher };
}
beforeEach(() => vi.restoreAllMocks());
describe('bounded conversation pages', () => {
  it('preserves host prefixes and uses the facade to support retained gateways', () => {
    expect(conversationUrl(socket).href).toBe(
      'https://thor.test/forge-host/build/api/v1/forge/sessions/session/conversation',
    );
    expect(conversationUrl('ws://pod.test/api/session').href).toBe(
      'http://pod.test/api/conversation/history',
    );
    expect(conversationUrl('/s/id/session').pathname).toBe(
      '/api/v1/forge/sessions/id/conversation',
    );
    expect(historySocketUrl(socket + '?custom=yes', true)).toContain(
      'custom=yes&history=recent&history_protocol=2&history_delivery=none',
    );
    expect(historySocketUrl(socket, false)).toBe(socket);
    expect(historySocketUrl(null, true)).toBeNull();
    expect(historySocketUrl('invalid', true)).toBe('invalid');
  });
  it.each([1, 2])(
    'loads exactly 50 initially, then 50 and the final 23 with protocol %s',
    async (version) => {
      const { calls } = serve(version);
      const first = await fetchHistoryBatch(socket, signal());
      expect(first.turns.map((t) => t.id)).toEqual(rows.slice(-50).map((t) => t.id));
      const older = await fetchHistoryBatch(socket, signal(), first);
      expect(older.turns.map((t) => t.id)).toEqual(rows.slice(23, 73).map((t) => t.id));
      const last = await fetchHistoryBatch(socket, signal(), older);
      expect(last.turns.map((t) => t.id)).toEqual(rows.slice(0, 23).map((t) => t.id));
      expect(last.window_offset).toBe(0);
      expect(
        calls.every((u) => u.searchParams.get('max_bytes') === String(HISTORY_PAGE_BYTES)),
      ).toBe(true);
      expect(calls.every((u) => Number(u.searchParams.get('limit')) <= 51)).toBe(true);
    },
  );
  it('retries the old offset seek when appends move the tail; joins by held identity', async () => {
    const { fetcher } = serve(1, 51);
    const first = await fetchHistoryBatch(socket, signal());
    let added = false;
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation(async (raw) => {
      if (!added) {
        rows.push(turn(123), turn(124));
        added = true;
      }
      return original(raw);
    });
    try {
      const older = await fetchHistoryBatch(socket, signal(), first);
      expect(older.turns.map((t) => t.id)).toEqual(rows.slice(23, 73).map((t) => t.id));
    } finally {
      rows.splice(123);
    }
  });
  it('rejects a rewritten legacy seam instead of silently skipping messages', async () => {
    const { fetcher } = serve(1, 51);
    const first = await fetchHistoryBatch(socket, signal());
    fetcher.mockResolvedValue(
      Response.json({
        turns: [turn(999)],
        total_turns: 123,
        window_offset: 73,
        projection_revision: 'same',
      }),
    );
    await expect(fetchHistoryBatch(socket, signal(), first)).rejects.toThrow('history changed');
  });
  it('reads before a huge verified legacy seam that occupies a page alone', async () => {
    const { fetcher } = serve(1, 50);
    const first = await fetchHistoryBatch(socket, signal());
    fetcher.mockResolvedValueOnce(
      Response.json({
        turns: [rows[73]],
        total_turns: 123,
        window_offset: 73,
        projection_revision: 'same',
      }),
    );
    const older = await fetchHistoryBatch(socket, signal(), first);
    expect(older.turns).toHaveLength(50);
    expect(older.turns.at(-1)?.id).toBe('row-72');
  });
  it('rejects an ignored cursor and an incorrect page boundary', async () => {
    const { fetcher } = serve();
    const first = await fetchHistoryBatch(socket, signal());
    fetcher.mockResolvedValueOnce(Response.json({ turns: [] }));
    await expect(fetchHistoryBatch(socket, signal(), first)).rejects.toThrow('boundary');
    fetcher.mockResolvedValueOnce(
      Response.json({
        turns: [],
        history_protocol: 2,
        total_turns: 123,
        window_offset: 0,
        window_end: 0,
      }),
    );
    await expect(fetchHistoryBatch(socket, signal(), first)).rejects.toThrow('history changed');
  });
  it.each([
    ['history_cursor_invalid', 'history changed'],
    ['conflict', 'HTTP 409'],
  ])('classifies structured conflict %s', async (code, message) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Response.json({ detail: { code } }, { status: 409 })),
    );
    await expect(fetchHistoryBatch(socket, signal())).rejects.toThrow(message);
  });
  it('bounds transfer before JSON parsing when an old broker ignores paging', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('x'.repeat(HISTORY_PAGE_BYTES + 1))),
    );
    await expect(fetchHistoryBatch(socket, signal())).rejects.toThrow('page size');
  });
  it.each([
    { turns: rows },
    { turns: [turn(1), turn(1)] },
    { turns: [{}] },
    { turns: [], window_offset: -1 },
    { turns: [], history_protocol: 3 },
    { turns: [], history_protocol: 2 },
  ])('rejects malformed or unbounded pages', async (data) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Response.json(data)),
    );
    await expect(fetchHistoryBatch(socket, signal())).rejects.toThrow();
  });
  it('reports unavailable history and preserves cancellation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(null, { status: 503 })),
    );
    await expect(fetchHistoryBatch(socket, signal())).rejects.toThrow('503');
    const controller = new AbortController();
    controller.abort();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_, init) => {
        init.signal.throwIfAborted();
      }),
    );
    await expect(fetchHistoryBatch(socket, controller.signal)).rejects.toThrow();
  });
  it('accepts complete small legacy histories without inventing a cursor', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Response.json({ turns: [turn(0)] })),
    );
    expect((await fetchHistoryBatch(socket, signal())).window_offset).toBe(0);
  });
  it('recognizes controls by type and code, never by warning prose', () => {
    expect(isHistoryRecovery({ type: 'history_gap' })).toBe(true);
    expect(isHistoryRecovery({ type: 'error', code: 'conversation_history_too_large' })).toBe(true);
    expect(
      isHistoryRecovery({ type: 'error', error: { code: 'conversation_history_too_large' } }),
    ).toBe(true);
    expect(
      isHistoryRecovery({
        type: 'error',
        code: 'permission_denied',
        error: { code: 'conversation_history_too_large' },
      }),
    ).toBe(false);
    expect(
      isHistoryRecovery({
        type: 'assistant',
        error: 'Conversation history is too large for WebSocket replay.',
      }),
    ).toBe(false);
  });
  it('only accepts an explicitly requested complete item', async () => {
    const fetcher = vi.fn(async () => Response.json({ turn: turn(0) }));
    vi.stubGlobal('fetch', fetcher);
    expect((await fetchHistoryItem(socket, 'row-0', signal())).id).toBe('row-0');
    expect(new URL(fetcher.mock.calls[0]![0] as string).searchParams.get('turn_id')).toBe('row-0');
    fetcher.mockResolvedValueOnce(Response.json({ turns: rows }));
    await expect(fetchHistoryItem(socket, 'row-0', signal())).rejects.toThrow('cannot expand');
    fetcher.mockResolvedValueOnce(Response.json({ turn: { ...turn(0), history_preview: true } }));
    await expect(fetchHistoryItem(socket, 'row-0', signal())).rejects.toThrow('cannot expand');
    fetcher.mockResolvedValueOnce(new Response(null, { status: 404 }));
    await expect(fetchHistoryItem(socket, 'row-0', signal())).rejects.toThrow('404');
  });
});
