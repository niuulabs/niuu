import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  conversationUrl,
  fetchHistoryBatch,
  fetchHistoryItem,
  HISTORY_PAGE_BYTES,
  HISTORY_REQUEST_BYTES,
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
    'loads 10 initially and only fetches preceding pages when requested with protocol %s',
    async (version) => {
      const { calls } = serve(version);
      const first = await fetchHistoryBatch(socket, signal());
      expect(first.turns.map((t) => t.id)).toEqual(rows.slice(-10).map((t) => t.id));
      expect(calls).toHaveLength(1);
      const older = await fetchHistoryBatch(socket, signal(), first);
      expect(older.turns.map((t) => t.id)).toEqual(rows.slice(103, 113).map((t) => t.id));
      let page = older;
      const collected = [...older.turns, ...first.turns];
      while (page.window_offset > 0) {
        page = await fetchHistoryBatch(socket, signal(), page);
        expect(page.turns.length).toBeLessThanOrEqual(10);
        collected.unshift(...page.turns);
      }
      expect(collected.map((t) => t.id)).toEqual(rows.map((t) => t.id));
      expect(page.turns).toHaveLength(3);
      expect(
        calls.every((u) => u.searchParams.get('max_bytes') === String(HISTORY_REQUEST_BYTES)),
      ).toBe(true);
      expect(calls.every((u) => Number(u.searchParams.get('limit')) <= 11)).toBe(true);
    },
  );
  it.each([1, 2])(
    'returns a small byte-limited page without chasing older rows (protocol %s)',
    async (version) => {
      const { calls } = serve(version, 2);
      const page = await fetchHistoryBatch(socket, signal());
      expect(page.turns.map((t) => t.id)).toEqual(['row-121', 'row-122']);
      expect(calls).toHaveLength(1);
      const older = await fetchHistoryBatch(socket, signal(), page);
      expect(older.turns.length).toBeGreaterThan(0);
      expect(calls).toHaveLength(2);
    },
  );
  it('keeps a continuous legacy boundary when appends shorten the next page', async () => {
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
      expect(older.turns.map((t) => t.id)).toEqual(rows.slice(105, 113).map((t) => t.id));
      const preceding = await fetchHistoryBatch(socket, signal(), older);
      expect(preceding.turns.map((t) => t.id)).toEqual(rows.slice(95, 105).map((t) => t.id));
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
        window_offset: 113,
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
        turns: [rows[113]],
        total_turns: 123,
        window_offset: 113,
        projection_revision: 'same',
      }),
    );
    const older = await fetchHistoryBatch(socket, signal(), first);
    expect(older.turns).toHaveLength(10);
    expect(older.turns.at(-1)?.id).toBe('row-112');
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
  it('allows retained facade envelope metadata without exceeding the hard browser limit', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (raw: string) => {
        const requested = Number(new URL(raw).searchParams.get('max_bytes'));
        expect(requested).toBeLessThan(HISTORY_PAGE_BYTES);
        return Response.json({ turns: [turn(0)], _prep: { padding: 'x'.repeat(requested) } });
      }),
    );
    expect((await fetchHistoryBatch(socket, signal())).turns.map((row) => row.id)).toEqual([
      'row-0',
    ]);
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
  it.each([false, true])(
    'automatically fills normal and metadata-only rows (metadata: %s)',
    async (metadata) => {
      const page = {
        turns: [
          {
            ...turn(0),
            content: 'Tail',
            history_preview: true,
            history_metadata_preview: metadata,
          },
          turn(1),
        ],
        total_turns: 2,
        window_offset: 0,
        window_end: 2,
        history_protocol: 2,
        projection_revision: 'same',
      };
      const complete = {
        ...turn(0),
        role: 'user',
        content: '# Complete message\n\n' + 'Text 📝 '.repeat(50000),
      };
      const controller = new AbortController();
      const fetcher = vi
        .fn()
        .mockResolvedValueOnce(Response.json(page))
        .mockResolvedValueOnce(Response.json({ turn: complete }));
      vi.stubGlobal('fetch', fetcher);
      const result = await fetchHistoryBatch(socket, controller.signal);
      expect(result.turns).toEqual([complete, turn(1)]);
      expect(result.window_end).toBe(2);
      expect(fetcher).toHaveBeenCalledTimes(2);
      const request = new URL(fetcher.mock.calls[1]![0]);
      expect(request.pathname).toBe('/forge-host/build/api/v1/forge/sessions/session/conversation');
      expect(request.searchParams.get('turn_id')).toBe('row-0');
      expect(request.searchParams.get('detail')).toBe('shallow');
      expect(fetcher.mock.calls[1]![1].signal).toBe(controller.signal);
    },
  );
  it('fills truncated older pages before prepending them', async () => {
    const current = {
      turns: [turn(1)],
      total_turns: 2,
      window_offset: 1,
      window_end: 2,
      history_protocol: 2,
      projection_revision: 'same',
      older_cursor: 'older',
    };
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({
          ...current,
          turns: [{ ...turn(0), history_preview: true }],
          window_offset: 0,
          window_end: 1,
          older_cursor: null,
        }),
      )
      .mockResolvedValueOnce(Response.json({ turn: turn(0) }));
    vi.stubGlobal('fetch', fetcher);
    const result = await fetchHistoryBatch(socket, signal(), current);
    expect(result.turns).toEqual([turn(0)]);
    expect(result.window_end).toBe(1);
  });
  it('does not expose an incomplete page when automatic message loading fails or is cancelled', async () => {
    const page = { turns: [{ ...turn(0), history_preview: true }] };
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json(page))
      .mockResolvedValueOnce(new Response(null, { status: 503 }));
    vi.stubGlobal('fetch', fetcher);
    await expect(fetchHistoryBatch(socket, signal())).rejects.toThrow('503');
    const controller = new AbortController();
    fetcher.mockResolvedValueOnce(Response.json(page)).mockImplementationOnce(async () => {
      controller.abort();
      return Response.json({ turn: turn(0) });
    });
    await expect(fetchHistoryBatch(socket, controller.signal)).rejects.toThrow();
  });
  it('only accepts an explicitly requested complete item', async () => {
    const fetcher = vi.fn(async () => Response.json({ turn: turn(0) }));
    vi.stubGlobal('fetch', fetcher);
    expect((await fetchHistoryItem(socket, 'row-0', signal())).id).toBe('row-0');
    expect(new URL(fetcher.mock.calls[0]![0] as string).searchParams.get('turn_id')).toBe('row-0');
    fetcher.mockResolvedValueOnce(Response.json({ turns: rows }));
    expect((await fetchHistoryItem(socket, 'row-0', signal())).id).toBe('row-0');
    fetcher.mockResolvedValueOnce(Response.json({ turns: rows.slice(1) }));
    await expect(fetchHistoryItem(socket, 'row-0', signal())).rejects.toThrow('cannot expand');
    fetcher.mockResolvedValueOnce(Response.json({ turns: [turn(0), turn(0)] }));
    await expect(fetchHistoryItem(socket, 'row-0', signal())).rejects.toThrow('cannot expand');
    fetcher.mockResolvedValueOnce(Response.json({ turn: { ...turn(0), history_preview: true } }));
    await expect(fetchHistoryItem(socket, 'row-0', signal())).rejects.toThrow('cannot expand');
    fetcher.mockResolvedValueOnce(new Response(null, { status: 404 }));
    await expect(fetchHistoryItem(socket, 'row-0', signal())).rejects.toThrow('404');
  });
});
