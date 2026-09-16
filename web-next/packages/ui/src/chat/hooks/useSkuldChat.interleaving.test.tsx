import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getStorageKey, useSkuldChat } from './useSkuldChat';

vi.mock('@niuulabs/query', () => ({
  getAccessToken: () => null,
  getAuthHeaders: () => new Headers(),
}));
let receive: (raw: string) => void;
vi.mock('./useWebSocket', () => ({
  useWebSocket: (_: unknown, handlers: { onMessage: typeof receive }) => {
    receive = handlers.onMessage;
    return { sendJson: vi.fn() };
  },
}));
const url = 'ws://example.invalid/s/session';
const emit = (event: Record<string, unknown>) => act(() => receive(JSON.stringify(event)));
const identified = (id: string, text: string, phase = 'commentary') => ({
  type: 'text',
  id,
  text,
  phase,
  turn_id: 'native-turn',
  thread_id: 'thread',
});
const complete = (id: string, text: string, phase = 'commentary') =>
  emit({
    type: 'assistant',
    turn_id: 'native-turn',
    message: { content: [identified(id, text, phase)] },
  });
const turn = (id: string, text: string, inProgress = false) => ({
  id,
  role: 'assistant',
  content: text,
  in_progress: inProgress,
  parts: [identified('a', text)],
  created_at: '2026-09-08T00:00:00Z',
});

beforeEach(() => {
  sessionStorage.clear();
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, json: async () => ({ turns: [] }) })),
  );
});

describe('identified text across browser live/history handoff', () => {
  it('keeps commentary/tool/final in one turn including whole tool completion and delayed results', async () => {
    const { result } = renderHook(() => useSkuldChat(url));
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    complete('a', 'Before');
    emit({
      type: 'content_block_start',
      turn_id: 'native-turn',
      content_block: { type: 'tool_use', id: 'tool', name: 'Bash', input: { command: 'pwd' } },
    });
    emit({
      type: 'assistant',
      turn_id: 'native-turn',
      message: {
        content: [{ type: 'tool_use', id: 'tool', name: 'Bash', input: { command: 'pwd' } }],
      },
    });
    complete('b', 'Final', 'final_answer');
    const toolResult = {
      type: 'content_block_start',
      turn_id: 'native-turn',
      content_block: { type: 'tool_result', tool_use_id: 'tool', content: '/workspace' },
    };
    emit(toolResult);
    emit(toolResult);
    emit({ type: 'result', turn_id: 'native-turn' });
    complete('b', 'Final', 'final_answer');
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]?.parts?.map((part) => part.id ?? part.tool_use_id)).toEqual([
      'a',
      'tool',
      'b',
      'tool',
    ]);
    expect(result.current.messages[0]?.content).toBe('Before\n\nFinal');
    expect(result.current.messages[0]?.status).toBe('done');
  });

  it('preserves a tools-first turn and an authoritative completion-only answer', async () => {
    const { result } = renderHook(() => useSkuldChat(url));
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    emit({
      type: 'content_block_start',
      turn_id: 'native-turn',
      content_block: { type: 'tool_use', id: 'tool', name: 'Bash' },
    });
    complete('a', 'Done', 'final_answer');
    complete('a', 'Done', 'final_answer');
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]?.parts?.map((part) => part.id)).toEqual(['tool', 'a']);
    expect(result.current.messages[0]?.content).toBe('Done');
  });

  it('socket snapshot wins delayed REST and accepts same-item completion in place', async () => {
    let deliver!: (value: unknown) => void;
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise((resolve) => {
            deliver = resolve;
          }),
      ),
    );
    const { result } = renderHook(() => useSkuldChat(url));
    emit({
      type: 'conversation_history',
      projection_revision: 'repair-2',
      turns: [turn('canonical', 'Newer', true)],
    });
    complete('a', 'Newer complete');
    await act(async () =>
      deliver({
        ok: true,
        json: async () => ({ projection_revision: 'repair-1', turns: [turn('canonical', 'Old')] }),
      }),
    );
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]?.content).toBe('Newer complete');
    expect(result.current.messages[0]?.parts).toHaveLength(1);
    emit({ type: 'result', turn_id: 'native-turn' });
    await waitFor(() =>
      expect(JSON.parse(sessionStorage.getItem(getStorageKey(url))!).projectionRevision).toBe(
        'repair-2',
      ),
    );
  });

  it('recovers the settled REST prefix without rolling back a newer token-only tail', async () => {
    let deliver!: (value: unknown) => void;
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise((resolve) => {
            deliver = resolve;
          }),
      ),
    );
    const { result } = renderHook(() => useSkuldChat(url));
    complete('live', 'Live now');
    await act(async () =>
      deliver({
        ok: true,
        json: async () => ({ turns: [turn('old', 'History'), turn('active', 'Stale', true)] }),
      }),
    );
    expect(result.current.messages.map((message) => message.content)).toEqual([
      'History',
      'Live now',
    ]);
  });

  it('harmless capability handshakes do not discard the initial history', async () => {
    let deliver!: (value: unknown) => void;
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise((resolve) => {
            deliver = resolve;
          }),
      ),
    );
    const { result } = renderHook(() => useSkuldChat(url));
    emit({ type: 'capabilities', interrupt: true });
    await act(async () =>
      deliver({ ok: true, json: async () => ({ turns: [turn('old', 'History')] }) }),
    );
    expect(result.current.messages[0]?.content).toBe('History');
  });

  it('repair revision replaces the cached prefix, then keeps a stable tool position on new tokens', async () => {
    const { result } = renderHook(() => useSkuldChat(url));
    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    emit({
      type: 'conversation_history',
      projection_revision: 'old',
      turns: [turn('canonical', 'Flattened')],
    });
    const active = turn('canonical', 'Before', true);
    active.parts.push({ type: 'tool_use', id: 'tool', name: 'Bash' } as never);
    emit({ type: 'conversation_history', projection_revision: 'repaired', turns: [active] });
    complete('b', 'After', 'final_answer');
    expect(result.current.messages[0]?.parts?.map((part) => part.id)).toEqual(['a', 'tool', 'b']);
    expect(result.current.messages[0]?.content).toBe('Before\n\nAfter');
  });
});

it('repairs a missing streamed prefix from REST while preserving a newer live text item', async () => {
  const { result } = renderHook(() => useSkuldChat(url));
  await waitFor(() => expect(result.current.historyLoaded).toBe(true));
  emit({
    type: 'content_block_delta',
    item_id: 'a',
    turn_id: 'native-turn',
    delta: { type: 'text_delta', text: 'suffix' },
  });
  let deliver!: (value: unknown) => void;
  vi.stubGlobal(
    'fetch',
    vi.fn(
      () =>
        new Promise((resolve) => {
          deliver = resolve;
        }),
    ),
  );
  emit({
    type: 'content_block_stop',
    item_id: 'a',
    turn_id: 'native-turn',
    complete: true,
    text_bytes: 13,
  });
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  complete('b', 'Newer final', 'final_answer');
  const canonical = turn('canonical', 'prefix suffix', true);
  canonical.parts[0] = { ...canonical.parts[0]!, complete: true } as never;
  await act(async () => deliver({ ok: true, json: async () => ({ turns: [canonical] }) }));
  await waitFor(() => expect(result.current.messages[0]?.parts?.[0]?.text).toBe('prefix suffix'));
  expect(result.current.messages[0]?.parts?.map((part) => part.id)).toEqual(['a', 'b']);
  expect(result.current.messages[0]?.content).toBe('prefix suffix\n\nNewer final');
});

it('does not fetch full history for a stop after an already-authoritative whole completion', async () => {
  const { result } = renderHook(() => useSkuldChat(url));
  await waitFor(() => expect(result.current.historyLoaded).toBe(true));
  complete('a', 'abc', 'final_answer');
  vi.stubGlobal('fetch', vi.fn());
  emit({
    type: 'content_block_stop',
    item_id: 'a',
    turn_id: 'native-turn',
    complete: true,
    text_bytes: 3,
    text_sha256: 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
  });
  expect(fetch).not.toHaveBeenCalled();
  expect(result.current.messages[0]?.content).toBe('abc');
});

it('seals optimized text only after matching bytes and digest', async () => {
  const { result } = renderHook(() => useSkuldChat(url));
  await waitFor(() => expect(result.current.historyLoaded).toBe(true));
  emit({
    type: 'content_block_delta',
    item_id: 'a',
    turn_id: 'native-turn',
    delta: { type: 'text_delta', text: 'abc' },
  });
  emit({
    type: 'content_block_stop',
    item_id: 'a',
    turn_id: 'native-turn',
    complete: true,
    phase: 'final_answer',
    text_bytes: 3,
    text_sha256: 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
  });
  await waitFor(() => expect(result.current.messages[0]?.parts?.[0]?.complete).toBe(true));
  expect(result.current.messages[0]?.parts?.[0]?.phase).toBe('final_answer');
  expect(result.current.messages[0]?.content).toBe('abc');
});

it('a failed authoritative repair keeps observed text incomplete without invented output', async () => {
  const { result } = renderHook(() => useSkuldChat(url));
  await waitFor(() => expect(result.current.historyLoaded).toBe(true));
  emit({
    type: 'content_block_delta',
    item_id: 'a',
    turn_id: 'native-turn',
    delta: { type: 'text_delta', text: 'tail' },
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: false, status: 503 })),
  );
  emit({
    type: 'content_block_stop',
    item_id: 'a',
    turn_id: 'native-turn',
    complete: true,
    text_bytes: 99,
  });
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  expect(result.current.messages[0]?.parts?.[0]?.complete).toBe(false);
  expect(result.current.messages[0]?.content).toBe('tail');
});

it('an older repair cannot overwrite a whole completion received while its GET was pending', async () => {
  const { result } = renderHook(() => useSkuldChat(url));
  await waitFor(() => expect(result.current.historyLoaded).toBe(true));
  emit({
    type: 'content_block_delta',
    item_id: 'a',
    turn_id: 'native-turn',
    delta: { type: 'text_delta', text: 'tail' },
  });
  let deliver!: (value: unknown) => void;
  vi.stubGlobal(
    'fetch',
    vi.fn(
      () =>
        new Promise((resolve) => {
          deliver = resolve;
        }),
    ),
  );
  emit({
    type: 'content_block_stop',
    item_id: 'a',
    turn_id: 'native-turn',
    complete: true,
    text_bytes: 99,
  });
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  complete('a', 'Newest authoritative text', 'final_answer');
  const canonical = turn('canonical', 'Older completion');
  canonical.parts[0] = { ...canonical.parts[0]!, complete: true } as never;
  await act(async () => deliver({ ok: true, json: async () => ({ turns: [canonical] }) }));
  expect(result.current.messages[0]?.content).toBe('Newest authoritative text');
});

it('inserts an entirely missing text item at the authoritative position between observed anchors', async () => {
  const { result } = renderHook(() => useSkuldChat(url));
  await waitFor(() => expect(result.current.historyLoaded).toBe(true));
  complete('a', 'Before');
  const tool = { type: 'tool_use', id: 'tool', name: 'Bash', input: { command: 'pwd' } };
  emit({ type: 'content_block_start', turn_id: 'native-turn', content_block: tool });
  let deliver!: (value: unknown) => void;
  vi.stubGlobal(
    'fetch',
    vi.fn(
      () =>
        new Promise((resolve) => {
          deliver = resolve;
        }),
    ),
  );
  emit({
    type: 'content_block_stop',
    item_id: 'missing',
    turn_id: 'native-turn',
    complete: true,
    text_bytes: 7,
  });
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  complete('later', 'Newer final', 'final_answer');
  const canonical = {
    ...turn('canonical', 'Before\n\nMissing', true),
    parts: [
      { ...identified('a', 'Before'), complete: true },
      tool,
      { ...identified('missing', 'Missing'), complete: true },
    ],
  };
  await act(async () => deliver({ ok: true, json: async () => ({ turns: [canonical] }) }));
  await waitFor(() =>
    expect(result.current.messages[0]?.parts?.map((part) => part.id)).toEqual([
      'a',
      'tool',
      'missing',
      'later',
    ]),
  );
  expect(result.current.messages[0]?.content).toBe('Before\n\nMissing\n\nNewer final');
  expect(result.current.messages).toHaveLength(1);
});
