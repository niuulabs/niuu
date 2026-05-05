import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useSkuldChat } from './useSkuldChat';

vi.mock('@niuulabs/query', () => ({
  getAccessToken: () => null,
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

describe('useSkuldChat', () => {
  beforeEach(() => {
    sendJson.mockReset();
    wsHandlers = {};
    sessionStorage.clear();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ turns: [] }),
      })),
    );
  });

  it('updates participant status on room_activity after room_state', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_state',
          participants: [
            {
              peer_id: 'peer-1',
              persona: 'Ravn-A',
              participant_type: 'ravn',
            },
          ],
        }),
      );
    });

    expect(result.current.participants.get('peer-1')?.status).toBeUndefined();

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_activity',
          participantId: 'peer-1',
          activityType: 'thinking',
        }),
      );
    });

    expect(result.current.participants.get('peer-1')?.status).toBe('thinking');
  });

  it('hydrates history from websocket conversation_history events', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'conversation_history',
          turns: [
            {
              id: 'turn-1',
              role: 'user',
              content: 'Kick off the raid.',
              created_at: '2026-05-01T10:18:36.889091+00:00',
            },
          ],
        }),
      );
    });

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]?.content).toBe('Kick off the raid.');
  });

  it('renders live user_confirmed events without requiring a refresh', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'user_confirmed',
          id: 'telegram-turn-1',
          content: 'Can you tell me the participants in this chat',
          created_at: '2026-05-03T13:24:30.988625+00:00',
          metadata: { source_platform: 'telegram' },
        }),
      );
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]?.id).toBe('telegram-turn-1');
    expect(result.current.messages[0]?.role).toBe('user');
    expect(result.current.messages[0]?.content).toBe(
      'Can you tell me the participants in this chat',
    );
    expect(result.current.messages[0]?.metadata).toEqual({ source_platform: 'telegram' });
  });

  it('ignores unsuccessful room_outcome events', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_outcome',
          participantId: 'peer-1',
          participant: { peer_id: 'peer-1', persona: 'reviewer', participant_type: 'ravn' },
          persona: 'reviewer',
          eventType: 'review.completed',
          fields: { success: false },
          summary: 'LLM backend unavailable',
        }),
      );
    });

    expect(result.current.meshEvents).toHaveLength(0);
    expect(result.current.messages).toHaveLength(0);
  });

  it('renders successful room_outcome events as outcome messages', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_outcome',
          participantId: 'peer-1',
          participant: { peer_id: 'peer-1', persona: 'reviewer', participant_type: 'ravn' },
          persona: 'reviewer',
          eventType: 'review.completed',
          verdict: 'needs_changes',
          summary: 'Found two regressions.',
          fields: {
            verdict: 'needs_changes',
            summary: 'Found two regressions.',
            findings_count: 2,
            files: ['README.md', 'docs/api.md'],
          },
        }),
      );
    });

    expect(result.current.meshEvents).toHaveLength(1);
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]?.participant?.persona).toBe('reviewer');
    expect(result.current.messages[0]?.content).toContain('```outcome');
    expect(result.current.messages[0]?.content).toContain('verdict: needs_changes');
    expect(result.current.messages[0]?.content).toContain('summary: Found two regressions.');
    expect(result.current.messages[0]?.content).toContain('findings_count: 2');
    expect(result.current.messages[0]?.content).toContain('files: ["README.md","docs/api.md"]');
  });

  it('serializes multiline room_outcome fields as yaml block scalars', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_outcome',
          participantId: 'peer-1',
          participant: { peer_id: 'peer-1', persona: 'reviewer', participant_type: 'ravn' },
          persona: 'reviewer',
          eventType: 'review.completed',
          verdict: 'conditional',
          summary: 'Needs a closer look.',
          fields: {
            verdict: 'conditional',
            summary: 'Needs a closer look.',
            findings:
              '## Verified\n- **Route pair count**: 26\n- Use `/api/v1/credentials/secrets`',
          },
        }),
      );
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]?.content).toContain('findings: |');
    expect(result.current.messages[0]?.content).toContain('  ## Verified');
    expect(result.current.messages[0]?.content).toContain('  - **Route pair count**: 26');
  });

  it('captures room_agent_event frames per participant', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_state',
          participants: [{ peer_id: 'peer-1', persona: 'Ravn-A', participant_type: 'ravn' }],
        }),
      );
    });

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_agent_event',
          participantId: 'peer-1',
          frame: {
            type: 'thought',
            data: 'Need to inspect the config first.',
            metadata: { severity: 'info' },
          },
        }),
      );
    });

    const events = result.current.agentEvents.get('peer-1');
    expect(events).toHaveLength(1);
    expect(events?.[0]?.frameType).toBe('thought');
    expect(events?.[0]?.data).toBe('Need to inspect the config first.');
    const running = result.current.messages.at(-1);
    expect(running?.status).toBe('running');
    expect(running?.visibility).toBe('internal');
    expect(running?.parts).toEqual([
      { type: 'reasoning', text: 'Need to inspect the config first.' },
    ]);
  });

  it('coalesces streamed room_agent_event text chunks without inserting newlines', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_state',
          participants: [{ peer_id: 'peer-1', persona: 'coder', participant_type: 'ravn' }],
        }),
      );
    });

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_agent_event',
          participantId: 'peer-1',
          frame: { type: 'message', data: 'The ' },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_agent_event',
          participantId: 'peer-1',
          frame: { type: 'message', data: 'smoke ' },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_agent_event',
          participantId: 'peer-1',
          frame: { type: 'message', data: 'test' },
        }),
      );
    });

    const running = result.current.messages.at(-1);
    expect(running?.content).toBe('The smoke test');
    expect(running?.parts).toEqual([{ type: 'text', text: 'The smoke test' }]);
  });

  it('creates a single Skuld participant for non-room assistant sessions', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'assistant',
          message: {
            model: 'claude-sonnet-4-6',
            content: [{ type: 'text', text: 'Hello from Skuld.' }],
          },
        }),
      );
    });

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'result',
          result: 'Hello from Skuld.',
        }),
      );
    });

    expect(Array.from(result.current.participants.values())[0]?.persona).toBe('Skuld');
    expect(result.current.messages.at(-1)?.participant?.persona).toBe('Skuld');
  });

  it('surfaces single-agent tool use while streaming', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'assistant',
          message: {
            model: 'claude-sonnet-4-6',
            content: [],
          },
        }),
      );
    });

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'content_block_start',
          content_block: {
            type: 'tool_use',
            id: 'tool-1',
            name: 'Read',
          },
        }),
      );
    });

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'content_block_delta',
          delta: {
            type: 'input_json_delta',
            partial_json: '{"file_path":"/tmp/demo.ts"}',
          },
        }),
      );
    });

    act(() => {
      wsHandlers.onMessage?.(JSON.stringify({ type: 'content_block_stop' }));
    });

    const message = result.current.messages.at(-1);
    expect(message?.status).toBe('running');
    expect(message?.parts).toEqual([
      {
        type: 'tool_use',
        id: 'tool-1',
        name: 'Read',
        input: { file_path: '/tmp/demo.ts' },
      },
    ]);
  });

  it('replaces a stale empty streaming assistant when a second assistant event arrives', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'assistant',
          message: { model: 'claude-sonnet-4-6', content: [] },
        }),
      );
    });

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'assistant',
          message: { model: 'claude-sonnet-4-6', content: [] },
        }),
      );
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]?.status).toBe('running');
  });

  it('does not revive stale running messages from session storage', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise(() => {
            // Keep history pending so we can inspect revived cache state directly.
          }),
      ),
    );

    sessionStorage.setItem(
      'niuu.skuldChat.ws://localhost:8080/s/test/session',
      JSON.stringify({
        messages: [
          {
            id: 'stale-running',
            role: 'assistant',
            content: '',
            createdAt: new Date().toISOString(),
            status: 'running',
            parts: [{ type: 'reasoning', text: 'old thinking' }],
          },
          {
            id: 'done-message',
            role: 'assistant',
            content: 'done',
            createdAt: new Date().toISOString(),
            status: 'done',
          },
        ],
      }),
    );

    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]?.id).toBe('done-message');
  });
});
