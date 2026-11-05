import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { transformTurns, useSkuldChat } from './useSkuldChat';

vi.mock('@niuulabs/query', () => ({
  getAccessToken: () => null,
  getAuthHeaders: (headers?: HeadersInit) => new Headers(headers),
}));

const sendJson = vi.fn();
let wsHandlers: {
  onOpen?: () => void;
  onMessage?: (raw: string) => void;
  onClose?: () => void;
  onError?: () => void;
} = {};

vi.mock('./useWebSocket', () => ({
  useWebSocket: (_url: string | null, handlers: typeof wsHandlers) => {
    wsHandlers = handlers;
    return { sendJson };
  },
}));

const URL_A = 'ws://localhost:8080/s/test/session';

function frame(value: Record<string, unknown>) {
  act(() => {
    wsHandlers.onMessage?.(JSON.stringify(value));
  });
}

async function connect(url: string = URL_A) {
  const hook = renderHook(
    ({ socketUrl }: { socketUrl: string }) => useSkuldChat(socketUrl, { historyMode: 'none' }),
    { initialProps: { socketUrl: url } },
  );
  await waitFor(() => expect(hook.result.current.historyLoaded).toBe(true));
  act(() => wsHandlers.onOpen?.());
  return hook;
}

describe('useSkuldChat session frames', () => {
  beforeEach(() => {
    sendJson.mockReset();
    wsHandlers = {};
    sessionStorage.clear();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, json: async () => ({ turns: [] }) })),
    );
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('plan', () => {
    it('starts empty and holds the latest plan frame', async () => {
      const { result } = await connect();
      expect(result.current.plan).toBeNull();

      frame({
        type: 'plan',
        tasks: [
          { content: 'Read', status: 'completed' },
          { content: 'Write', status: 'in_progress', activeForm: 'Writing' },
        ],
        counts: { total: 2 },
      });
      expect(result.current.plan).toEqual({
        tasks: [
          { content: 'Read', status: 'completed' },
          { content: 'Write', status: 'in_progress', activeForm: 'Writing' },
        ],
        counts: { total: 2, pending: 0, in_progress: 1, completed: 1 },
      });

      frame({ type: 'plan', tasks: [{ content: 'Only', status: 'pending' }] });
      expect(result.current.plan?.tasks).toHaveLength(1);
    });

    it('ignores a plan frame that has no task list', async () => {
      const { result } = await connect();
      frame({ type: 'plan', tasks: [{ content: 'A', status: 'pending' }] });
      frame({ type: 'plan' });
      expect(result.current.plan?.tasks).toHaveLength(1);
    });

    it('forgets the plan of the previous session when the url changes', async () => {
      const { result, rerender } = await connect();
      frame({ type: 'plan', tasks: [{ content: 'A', status: 'pending' }] });
      expect(result.current.plan).not.toBeNull();

      rerender({ socketUrl: 'ws://localhost:8080/s/other/session' });
      expect(result.current.plan).toBeNull();
      expect(result.current.agents).toEqual([]);
    });
  });

  describe('agents', () => {
    const started = (id: string, extra: Record<string, unknown> = {}) => ({
      type: 'agent_update',
      action: 'started',
      agent: { id, kind: 'subagent', name: `agent-${id}`, status: 'running', ...extra },
    });

    it('upserts by id and keeps finished agents with their status', async () => {
      const { result } = await connect();
      frame(started('a1'));
      frame(started('a2'));
      frame(started('a1', { description: 'now with a description' }));
      expect(result.current.agents.map((agent) => agent.id)).toEqual(['a1', 'a2']);
      expect(result.current.agents[0]?.description).toBe('now with a description');

      frame({
        type: 'agent_update',
        action: 'stopped',
        agent: { id: 'a1', kind: 'subagent', name: 'agent-a1', status: 'done', ended_at: 'T1' },
      });
      expect(result.current.agents).toEqual([
        expect.objectContaining({ id: 'a1', status: 'done', endedAt: 'T1' }),
        expect.objectContaining({ id: 'a2', status: 'running' }),
      ]);
    });

    it('ignores a malformed agent frame', async () => {
      const { result } = await connect();
      frame({ type: 'agent_update', action: 'started', agent: { name: 'no id' } });
      expect(result.current.agents).toEqual([]);
    });

    it('drops running agents on reconnect (the broker replays the live ones) but keeps finished', async () => {
      const { result } = await connect();
      frame(started('live'));
      frame(started('gone'));
      frame({
        type: 'agent_update',
        action: 'stopped',
        agent: { id: 'gone', kind: 'subagent', name: 'agent-gone', status: 'failed' },
      });
      act(() => wsHandlers.onOpen?.());
      expect(result.current.agents).toEqual([expect.objectContaining({ id: 'gone' })]);

      frame(started('live'));
      expect(result.current.agents.map((agent) => agent.id)).toEqual(['gone', 'live']);
    });
  });

  describe('effort and runtime options', () => {
    const effortStatus = {
      type: 'effort_status',
      request_id: 'r',
      current: 'medium',
      levels: ['low', 'medium', 'high'],
      mutable: true,
      applies_to: 'next_turn',
      source: 'native',
    };
    const runtimeOptions = {
      type: 'runtime_options',
      request_id: 'r',
      applied: false,
      source: 'native',
      applies_to: 'next_turn',
      models: [
        {
          id: 'm1',
          model: 'm1',
          name: 'Model One',
          description: '',
          default: true,
          hidden: false,
          effort_levels: ['low', 'high'],
          default_effort: 'high',
        },
      ],
      current: { model: 'm1', effort: 'high', service_tier: null },
    };

    it('stores the effort and runtime option frames', async () => {
      const { result } = await connect();
      expect(result.current.effort).toBeNull();
      expect(result.current.runtimeOptions).toBeNull();

      frame(effortStatus);
      frame(runtimeOptions);
      expect(result.current.effort).toEqual({
        current: 'medium',
        levels: ['low', 'medium', 'high'],
        mutable: true,
        appliesTo: 'next_turn',
      });
      expect(result.current.runtimeOptions).toMatchObject({
        current: { model: 'm1', effort: 'high' },
        models: [expect.objectContaining({ model: 'm1', name: 'Model One', isDefault: true })],
      });
    });

    it('ignores frames that are not shaped like the contract', async () => {
      const { result } = await connect();
      frame({ type: 'effort_status', levels: 'low' });
      frame({ type: 'runtime_options', models: [] });
      expect(result.current.effort).toBeNull();
      expect(result.current.runtimeOptions).toBeNull();
    });

    it('asks for effort over the socket only for runtimes without a model catalog', async () => {
      await connect();
      sendJson.mockClear();
      frame({ type: 'capabilities', set_effort: true });
      expect(sendJson).toHaveBeenCalledWith({
        type: 'get_effort',
        request_id: expect.any(String),
      });

      sendJson.mockClear();
      frame({ type: 'capabilities', set_effort: true, runtime_options: true });
      // Never over the socket: a refused socket request is written into the session history.
      expect(sendJson).not.toHaveBeenCalledWith(
        expect.objectContaining({ type: 'get_runtime_options' }),
      );
      expect(sendJson).not.toHaveBeenCalledWith(expect.objectContaining({ type: 'get_effort' }));
    });

    it('reads the model catalog over the broker HTTP route and takes effort from it', async () => {
      const fetchMock = vi.fn(async (input: string) =>
        String(input).endsWith('/api/runtime-options')
          ? { ok: true, status: 200, json: async () => runtimeOptions }
          : { ok: true, status: 200, json: async () => ({ turns: [] }) },
      );
      vi.stubGlobal('fetch', fetchMock);
      const { result } = await connect();
      frame({ type: 'capabilities', set_effort: true, runtime_options: true });

      await waitFor(() => expect(result.current.runtimeOptions?.current.model).toBe('m1'));
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/s\/test\/api\/runtime-options$/),
        expect.objectContaining({ headers: expect.anything() }),
      );
      expect(result.current.effort).toEqual({
        current: 'high',
        levels: ['low', 'high'],
        mutable: true,
        appliesTo: 'next_turn',
      });
      expect(result.current.runtimeOptionsError).toBeNull();
    });

    it('keeps a refused catalog read on the model chip, not in the conversation', async () => {
      vi.stubGlobal(
        'fetch',
        vi.fn(async (input: string) =>
          String(input).endsWith('/api/runtime-options')
            ? {
                ok: false,
                status: 502,
                json: async () => ({
                  detail: 'Codex runtime options require a connected native thread',
                }),
              }
            : { ok: true, status: 200, json: async () => ({ turns: [] }) },
        ),
      );
      const { result } = await connect();
      const before = result.current.messages.length;
      frame({ type: 'capabilities', runtime_options: true });

      await waitFor(() =>
        expect(result.current.runtimeOptionsError).toBe(
          'Codex runtime options require a connected native thread',
        ),
      );
      expect(result.current.messages).toHaveLength(before);
      expect(result.current.runtimeOptions).toBeNull();
    });

    it('does not ask a session that supports neither', async () => {
      await connect();
      sendJson.mockClear();
      frame({ type: 'capabilities', interrupt: true });
      expect(sendJson).not.toHaveBeenCalled();
    });

    it('exposes the steering capabilities the broker advertises', async () => {
      const { result } = await connect();
      frame({
        type: 'capabilities',
        steer: true,
        steering_mode: 'live',
        set_effort: true,
        runtime_options: true,
      });
      expect(result.current.capabilities).toMatchObject({
        steer: true,
        steering_mode: 'live',
        set_effort: true,
        runtime_options: true,
      });
    });

    it('sends set_effort with a request id', async () => {
      const { result } = await connect();
      sendJson.mockClear();
      act(() => result.current.sendSetEffort('high'));
      expect(sendJson).toHaveBeenCalledWith({
        type: 'set_effort',
        effort: 'high',
        request_id: expect.any(String),
      });
    });

    it('sends set_runtime_options with a request id', async () => {
      const { result } = await connect();
      sendJson.mockClear();
      act(() => result.current.sendSetRuntimeOptions({ model: 'm1', effort: 'low' }));
      expect(sendJson).toHaveBeenCalledWith({
        type: 'set_runtime_options',
        options: { model: 'm1', effort: 'low' },
        request_id: expect.any(String),
      });
    });

    it('refuses to send an empty runtime option change', async () => {
      const { result } = await connect();
      sendJson.mockClear();
      expect(() => result.current.sendSetRuntimeOptions({})).toThrow(/model, an effort/);
      expect(sendJson).not.toHaveBeenCalled();
    });

    it('surfaces a refused change through the error path', async () => {
      const { result } = await connect();
      act(() => result.current.sendSetEffort('turbo'));
      const requestId = sendJson.mock.calls.at(-1)?.[0]?.request_id as string;
      frame({
        type: 'error',
        code: 'control_message_rejected',
        request_id: requestId,
        content: 'Effort turbo is not one of low, medium, high',
      });
      expect(result.current.messages.at(-1)).toMatchObject({
        role: 'system',
        status: 'error',
        content: 'Effort turbo is not one of low, medium, high',
      });
    });

    it('names the broker it could not reach when the catalog request fails outright', async () => {
      vi.stubGlobal(
        'fetch',
        vi.fn(async (input: string) => {
          if (String(input).endsWith('/api/runtime-options'))
            throw new TypeError('Failed to fetch');
          return { ok: true, status: 200, json: async () => ({ turns: [] }) };
        }),
      );
      const { result } = await connect();
      frame({ type: 'capabilities', runtime_options: true });
      await waitFor(() =>
        expect(result.current.runtimeOptionsError).toMatch(
          /^Could not reach .+ for the model list \(Failed to fetch\)$/,
        ),
      );
    });

    it('keeps a refused effort read on the effort chip, not in the conversation', async () => {
      const { result } = await connect();
      sendJson.mockClear();
      frame({ type: 'capabilities', set_effort: true });
      const requestId = sendJson.mock.calls.at(-1)?.[0]?.request_id as string;
      const before = result.current.messages.length;
      frame({
        type: 'error',
        request_id: requestId,
        content: 'This harness does not expose an effort control',
      });
      expect(result.current.messages).toHaveLength(before);
      expect(result.current.effortError).toBe('This harness does not expose an effort control');

      frame({ type: 'effort_status', current: 'low', levels: ['low'], mutable: true });
      expect(result.current.effortError).toBeNull();
    });
  });

  describe('usage', () => {
    it('stores Codex session_usage', async () => {
      const { result } = await connect();
      expect(result.current.usage).toBeNull();
      frame({
        type: 'session_usage',
        model: 'gpt-5',
        last: { input: 10, cache_read: 90, cache_write: 0, output: 5 },
        total: { input: 20, cache_read: 180, cache_write: 0, output: 50 },
        window: 200000,
      });
      expect(result.current.usage).toEqual({
        model: 'gpt-5',
        contextTokens: 100,
        window: 200000,
        totalInputTokens: 200,
        totalOutputTokens: 50,
      });
    });

    it('ignores a usage frame that reports nothing', async () => {
      const { result } = await connect();
      frame({ type: 'session_usage', model: 'gpt-5', last: null, total: null, window: null });
      expect(result.current.usage).toBeNull();
    });
  });

  describe('message delivery', () => {
    async function sendOne(text = 'hello') {
      const hook = await connect();
      await act(async () => {
        hook.result.current.sendMessage(text, []);
      });
      const requestId = sendJson.mock.calls.at(-1)?.[0]?.request_id as string;
      return { ...hook, requestId };
    }

    it('has no delivery state until the broker reports one', async () => {
      const { result } = await sendOne();
      expect(result.current.messages[0]?.delivery).toBeUndefined();
    });

    it('marks a message queued when the broker confirms it as pending', async () => {
      const { result, requestId } = await sendOne();
      frame({
        type: 'user_confirmed',
        id: 'm1',
        request_id: requestId,
        content: 'hello',
        steering_state: 'pending',
      });
      expect(result.current.messages[0]).toMatchObject({
        id: 'm1',
        delivery: { state: 'queued' },
      });
    });

    it('moves queued to delivered to active, and never back', async () => {
      const { result, requestId } = await sendOne();
      frame({
        type: 'user_confirmed',
        id: 'm1',
        request_id: requestId,
        content: 'hello',
        steering_state: 'pending',
      });
      frame({ type: 'user_delivered', id: 'm1', request_id: requestId, status: 'delivered' });
      expect(result.current.messages[0]?.delivery).toEqual({ state: 'delivered' });

      frame({ type: 'user_active', id: 'm1', request_id: requestId });
      expect(result.current.messages[0]?.delivery).toEqual({ state: 'active' });

      frame({ type: 'user_delivered', id: 'm1' });
      frame({ type: 'user_delivery_failed', id: 'm1', error: 'late failure' });
      expect(result.current.messages[0]?.delivery).toEqual({ state: 'active' });
    });

    it('treats user_consumed (named by msg_id) as the agent taking the message', async () => {
      const { result, requestId } = await sendOne();
      frame({
        type: 'user_confirmed',
        id: 'm1',
        request_id: requestId,
        content: 'hello',
        steering_state: 'pending',
      });
      frame({ type: 'user_consumed', msg_id: 'm1', request_id: requestId });
      expect(result.current.messages[0]?.delivery).toEqual({ state: 'active' });
    });

    it('matches an unconfirmed optimistic message by its request id', async () => {
      const { result, requestId } = await sendOne();
      frame({ type: 'user_delivery_pending', id: 'm1', request_id: requestId, error: 'not saved' });
      expect(result.current.messages[0]?.delivery).toEqual({
        state: 'unconfirmed',
        error: 'not saved',
      });
    });

    it('records a failed delivery with the broker sentence', async () => {
      const { result, requestId } = await sendOne();
      frame({
        type: 'user_confirmed',
        id: 'm1',
        request_id: requestId,
        content: 'hello',
        steering_state: 'pending',
      });
      frame({
        type: 'user_delivery_failed',
        id: 'm1',
        request_id: requestId,
        error: 'The pane did not accept the message',
      });
      expect(result.current.messages[0]?.delivery).toEqual({
        state: 'failed',
        error: 'The pane did not accept the message',
      });
    });

    it('only touches the named user message', async () => {
      const { result, requestId } = await sendOne('first');
      frame({
        type: 'user_confirmed',
        id: 'm1',
        request_id: requestId,
        content: 'first',
        steering_state: 'pending',
      });
      frame({ type: 'user_confirmed', id: 'm2', content: 'second', steering_state: 'pending' });
      frame({ type: 'user_active', id: 'm2' });
      expect(result.current.messages.map((message) => message.delivery?.state)).toEqual([
        'queued',
        'active',
      ]);
    });

    it('adds a delivery state to a user message confirmed from another client', async () => {
      const { result } = await connect();
      frame({
        type: 'user_confirmed',
        id: 'm9',
        content: 'from elsewhere',
        steering_state: 'active',
      });
      expect(result.current.messages[0]).toMatchObject({
        id: 'm9',
        delivery: { state: 'active' },
      });
    });

    it('leaves delivery unset when the confirmation carries no steering state', async () => {
      const { result } = await connect();
      frame({ type: 'user_confirmed', id: 'm9', content: 'workflow prompt' });
      expect(result.current.messages[0]?.delivery).toBeUndefined();
    });

    it('ignores a delivery frame for a message it does not have', async () => {
      const { result } = await connect();
      frame({ type: 'user_active', id: 'unknown' });
      expect(result.current.messages).toEqual([]);
    });
  });

  describe('history', () => {
    const turn = (id: string, role: string, steering?: string) => ({
      id,
      role,
      content: id,
      created_at: '2026-11-05T10:00:00Z',
      metadata: steering ? { steering_state: steering } : {},
    });

    it('maps the broker steering_state of user turns onto delivery', () => {
      const messages = transformTurns([
        turn('u1', 'user', 'pending'),
        turn('u2', 'user', 'active'),
        turn('u3', 'user', 'failed'),
        turn('u4', 'user'),
        turn('a1', 'assistant', 'pending'),
      ]);
      expect(messages.map((message) => message.delivery)).toEqual([
        { state: 'queued' },
        { state: 'active' },
        { state: 'failed' },
        undefined,
        undefined,
      ]);
    });
  });

  describe('input requests', () => {
    const ask = (requestId: string) => ({
      type: 'ask_user_question',
      request_id: requestId,
      questions: [{ question: 'Which?', options: [{ label: 'A' }, { label: 'B' }] }],
    });

    it('stamps when the question first arrived and keeps it when the question is replayed', async () => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date('2026-11-05T10:00:00Z'));
      const { result } = renderHook(() => useSkuldChat(URL_A, { historyMode: 'none' }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      frame(ask('q1'));
      expect(result.current.pendingInputRequests[0]?.receivedAt).toBe('2026-11-05T10:00:00.000Z');

      vi.setSystemTime(new Date('2026-11-05T10:05:00Z'));
      frame(ask('q1'));
      expect(result.current.pendingInputRequests).toHaveLength(1);
      expect(result.current.pendingInputRequests[0]?.receivedAt).toBe('2026-11-05T10:00:00.000Z');

      frame(ask('q2'));
      expect(result.current.pendingInputRequests[1]?.receivedAt).toBe('2026-11-05T10:05:00.000Z');
    });
  });
});
