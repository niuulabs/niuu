import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  formatOutcomeContent,
  getStorageKey,
  parseEvent,
  parseParticipantMeta,
  pushOutcomeField,
  reviveAgentEvents,
  reviveMessages,
  reviveMeshEvents,
  serializeAgentEvents,
  serializeMessages,
  serializeMeshEvents,
  stringifyOutcomeValue,
  useSkuldChat,
} from './useSkuldChat';

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

  afterEach(() => {
    vi.useRealTimers();
  });

  it('parses participant metadata from both snake_case and camelCase fields', () => {
    expect(
      parseParticipantMeta({
        peerId: 'peer-1',
        persona: 'reviewer',
        displayName: 'Reviewer',
        participantType: 'ravn',
        gatewayUrl: 'wss://example.test',
        gatewayLatencyMs: 22,
        gatewayRegion: 'iad',
        status: 'thinking',
      }),
    ).toMatchObject({
      peerId: 'peer-1',
      persona: 'reviewer',
      displayName: 'Reviewer',
      participantType: 'ravn',
      gateway: 'wss://example.test',
      gatewayLatencyMs: 22,
      gatewayRegion: 'iad',
      status: 'thinking',
    });

    expect(parseParticipantMeta({ persona: 'missing-peer-id' })).toBeUndefined();
  });

  it('parses raw websocket events with and without SSE data prefixes', () => {
    expect(parseEvent('{"type":"room_state","participants":[]}')).toMatchObject({
      type: 'room_state',
    });
    expect(parseEvent('data: {"type":"assistant","content":"hi"}')).toMatchObject({
      type: 'assistant',
      content: 'hi',
    });
    expect(parseEvent('not-json')).toBeNull();
  });

  it('serializes and revives persisted message and event state', () => {
    const createdAt = new Date('2026-05-11T10:00:00Z');
    const timestamp = new Date('2026-05-11T10:05:00Z');
    const messages = serializeMessages([
      { id: 'done-1', role: 'assistant', content: 'persist me', createdAt, status: 'done' },
      { id: 'running-1', role: 'assistant', content: 'skip me', createdAt, status: 'running' },
    ]);
    expect(messages).toEqual([
      expect.objectContaining({ id: 'done-1', createdAt: '2026-05-11T10:00:00.000Z' }),
    ]);
    expect(reviveMessages(messages)).toEqual([
      expect.objectContaining({ id: 'done-1', createdAt }),
    ]);

    const meshEvents = serializeMeshEvents([
      {
        id: 'mesh-1',
        type: 'notification',
        timestamp,
        participantId: 'peer-1',
        participant: { color: 'brand' },
        persona: 'chair',
        notificationType: 'help_needed',
        summary: 'Need operator input',
        urgency: 0.8,
      },
    ]);
    expect(reviveMeshEvents(meshEvents)[0]).toMatchObject({ id: 'mesh-1', timestamp });

    const agentEvents = new Map([
      [
        'peer-1',
        [
          {
            id: 'agent-1',
            participantId: 'peer-1',
            frameType: 'thought',
            data: 'thinking',
            timestamp,
          },
        ],
      ],
    ]);
    expect(reviveAgentEvents(serializeAgentEvents(agentEvents)).get('peer-1')?.[0]).toMatchObject({
      id: 'agent-1',
      timestamp,
    });
  });

  it('formats outcome blocks and multiline field payloads for chat rendering', () => {
    const lines: string[] = [];
    pushOutcomeField(lines, 'notes', 'line one\nline two');
    pushOutcomeField(lines, 'empty', '');
    expect(lines).toEqual(['notes: |', '  line one', '  line two']);

    expect(stringifyOutcomeValue(null)).toBe('');
    expect(stringifyOutcomeValue(false)).toBe('false');
    expect(stringifyOutcomeValue({ reason: 'details' })).toBe('{"reason":"details"}');

    expect(
      formatOutcomeContent({
        type: 'room_outcome',
        eventType: 'review.completed',
        summary: 'Needs_changes,please_retry',
        fields: {
          verdict: 'needs_changes',
          summary: 'Needs_changes,please_retry',
          success: true,
          reasons: ['missing tests'],
        },
      }),
    ).toContain('summary: Needs_changes, please_retry');

    expect(
      formatOutcomeContent({
        type: 'room_outcome',
        eventType: 'research.completed',
      }),
    ).toContain('event_type: research.completed');
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

  it('uses fallback room_activity status fields and ignores unknown participants', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_state',
          participants: [
            {
              peer_id: 'peer-2',
              persona: 'Ravn-B',
              participant_type: 'ravn',
            },
          ],
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_activity',
          participant_id: 'peer-2',
          status: 'waiting',
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_activity',
          participantId: 'peer-missing',
          status: 'thinking',
        }),
      );
    });

    expect(result.current.participants.get('peer-2')?.status).toBe('waiting');
    expect(result.current.participants.has('peer-missing')).toBe(false);
  });

  it('skips session history fetch for warden-style live consoles', async () => {
    const fetchSpy = vi.fn(async () => ({
      ok: true,
      json: async () => ({ turns: [] }),
    }));
    vi.stubGlobal('fetch', fetchSpy);

    const { result } = renderHook(() =>
      useSkuldChat('ws://localhost:8080/wardens/test/ws', { historyMode: 'none' }),
    );

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('marks history as loaded when the websocket url cannot be mapped to history', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);

    const { result } = renderHook(() => useSkuldChat('not-a-valid-url'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('rehydrates cached state when the session url changes', async () => {
    const cachedUrl = 'ws://localhost:8080/s/next/session';
    sessionStorage.setItem(
      getStorageKey(cachedUrl),
      JSON.stringify({
        messages: [
          {
            id: 'cached-message-1',
            role: 'assistant',
            content: 'Cached answer.',
            createdAt: '2026-05-04T10:00:00.000Z',
            status: 'done',
          },
        ],
        participants: [
          {
            peerId: 'peer-cache',
            persona: 'reviewer',
            displayName: 'Reviewer',
            participantType: 'ravn',
            status: 'idle',
          },
        ],
        meshEvents: [
          {
            id: 'cached-mesh-1',
            type: 'notification',
            timestamp: '2026-05-04T10:01:00.000Z',
            participantId: 'peer-cache',
            participant: { color: 'brand' },
            persona: 'reviewer',
            notificationType: 'help_needed',
            summary: 'Cached mesh notification',
            urgency: 0.6,
          },
        ],
        agentEvents: {
          'peer-cache': [
            {
              id: 'cached-agent-1',
              participantId: 'peer-cache',
              frameType: 'thought',
              data: 'Cached internal event',
              timestamp: '2026-05-04T10:02:00.000Z',
            },
          ],
        },
      }),
    );

    const { result, rerender } = renderHook(
      ({ url }) => useSkuldChat(url, { historyMode: 'none' }),
      { initialProps: { url: 'ws://localhost:8080/s/test/session' } },
    );

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    rerender({ url: cachedUrl });

    await waitFor(() => expect(result.current.messages[0]?.content).toBe('Cached answer.'));
    expect(result.current.participants.get('peer-cache')).toMatchObject({
      persona: 'reviewer',
      status: 'idle',
    });
    expect(result.current.meshEvents[0]).toMatchObject({
      summary: 'Cached mesh notification',
    });
    expect(result.current.agentEvents.get('peer-cache')?.[0]).toMatchObject({
      data: 'Cached internal event',
    });
  });

  it('hydrates room_message events with participant, timestamp, and visibility metadata', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_message',
          role: 'assistant',
          content: 'Anonymous review packet is ready.',
          created_at: '2026-05-11T12:30:00.000Z',
          thread_id: 'thread-review-1',
          visibility: 'internal',
          participant: {
            peer_id: 'peer-chair',
            persona: 'council-chair',
            participant_type: 'ravn',
            display_name: 'Council Chair',
          },
        }),
      );
    });

    expect(result.current.messages[0]).toMatchObject({
      role: 'assistant',
      content: 'Anonymous review packet is ready.',
      threadId: 'thread-review-1',
      visibility: 'internal',
      participant: expect.objectContaining({ persona: 'council-chair' }),
    });
  });

  it('tracks participant join and leave events', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'participant_joined',
          participant: {
            peer_id: 'peer-join',
            persona: 'council-member-c',
            participant_type: 'ravn',
          },
        }),
      );
    });

    expect(result.current.participants.get('peer-join')?.persona).toBe('council-member-c');

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'participant_left',
          participantId: 'peer-join',
        }),
      );
    });

    expect(result.current.participants.has('peer-join')).toBe(false);
  });

  it('deduplicates user_confirmed events and ignores non-object metadata', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'user_confirmed',
          id: 'confirm-1',
          content: 'Proceed with the next council round.',
          metadata: 'not-an-object',
          visibility: 'internal',
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'user_confirmed',
          id: 'confirm-1',
          content: 'Proceed with the next council round.',
          metadata: { reason: 'duplicate' },
          visibility: 'internal',
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'user_confirmed',
          request_id: 'confirm-2',
          content: 'Use the request id when no explicit id is present.',
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'user_confirmed',
          content: 'Generate an id when the event does not provide one.',
        }),
      );
    });

    expect(result.current.messages).toHaveLength(3);
    expect(result.current.messages[0]).toMatchObject({
      id: 'confirm-1',
      role: 'user',
      content: 'Proceed with the next council round.',
      metadata: undefined,
      visibility: 'internal',
      status: 'done',
    });
    expect(result.current.messages[1]).toMatchObject({
      id: 'confirm-2',
      content: 'Use the request id when no explicit id is present.',
    });
    expect(result.current.messages[2]?.id).toBeTruthy();
  });

  it('ignores empty user_confirmed content and preserves optimistic metadata when confirmation metadata is absent', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    await act(async () => {
      result.current.sendMessage('Keep my optimistic metadata', []);
    });

    const requestId = sendJson.mock.calls.at(-1)?.[0]?.request_id as string;

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'user_confirmed',
          request_id: requestId,
          id: 'confirmed-user-1',
          content: 'Keep my optimistic metadata',
          created_at: '2026-05-05T10:00:00.000Z',
          visibility: 'internal',
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'user_confirmed',
          request_id: 'ignored-empty',
          content: '',
        }),
      );
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]).toMatchObject({
      id: 'confirmed-user-1',
      content: 'Keep my optimistic metadata',
      visibility: 'internal',
      attachments: [],
    });
    expect(result.current.messages[0]?.createdAt).toEqual(new Date('2026-05-05T10:00:00.000Z'));
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
              content: 'Kick off the run.',
              created_at: '2026-05-01T10:18:36.889091+00:00',
            },
          ],
        }),
      );
    });

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]?.content).toBe('Kick off the run.');
  });

  it('hydrates fetched assistant history with a synthesized Skuld participant when metadata is absent', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          turns: [
            {
              id: 'turn-fetch-1',
              role: 'assistant',
              content: 'Fetched history summary.',
              created_at: '2026-05-01T10:22:00.000000+00:00',
            },
          ],
        }),
      })),
    );

    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(result.current.messages[0]).toMatchObject({
      content: 'Fetched history summary.',
      participant: expect.objectContaining({
        peerId: 'skuld-primary',
        persona: 'Skuld',
        participantType: 'skuld',
      }),
    });
    expect(result.current.participants.get('skuld-primary')).toMatchObject({
      persona: 'Skuld',
    });
  });

  it('hydrates fetched history participants and mesh events from turn metadata', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          turns: [
            {
              id: 'turn-fetch-2',
              role: 'assistant',
              content: `---outcome---
event_type: review.completed
verdict: approved
summary: Fetched review packet ready
---end---`,
              created_at: '2026-05-01T10:25:00.000000+00:00',
              participant_meta: {
                peer_id: 'peer-fetch',
                persona: 'reviewer',
                participant_type: 'ravn',
                color: 'brand',
              },
            },
          ],
        }),
      })),
    );

    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(result.current.participants.get('peer-fetch')).toMatchObject({
      persona: 'reviewer',
      participantType: 'ravn',
    });
    expect(result.current.meshEvents[0]).toMatchObject({
      participantId: 'peer-fetch',
      eventType: 'review.completed',
      summary: 'Fetched review packet ready',
    });
  });

  it('retries failed history fetches and clears the retry timer when the url changes', async () => {
    vi.useFakeTimers();
    const clearTimeoutSpy = vi.spyOn(globalThis, 'clearTimeout');
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 503,
        json: async () => ({ turns: [] }),
      })),
    );

    const { result, rerender } = renderHook(({ url }) => useSkuldChat(url), {
      initialProps: { url: 'ws://localhost:8080/s/test/session' },
    });

    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.historyLoaded).toBe(false);

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    await act(async () => {
      rerender({ url: 'not-a-valid-url-after-retry' });
      await Promise.resolve();
    });

    expect(result.current.historyLoaded).toBe(true);
    expect(clearTimeoutSpy).toHaveBeenCalled();
  });

  it('hydrates participants from conversation_history turn metadata when room_state is missing', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'conversation_history',
          turns: [
            {
              id: 'turn-1',
              role: 'assistant',
              content: 'Opinion submitted.',
              created_at: '2026-05-01T10:18:36.889091+00:00',
              participant_id: 'flock-council-member-b',
              participant_meta: {
                peer_id: 'flock-council-member-b',
                persona: 'council-member-b',
                participant_type: 'ravn',
                display_name: 'council-member-b',
              },
            },
          ],
        }),
      );
    });

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(result.current.participants.get('flock-council-member-b')?.participantType).toBe('ravn');
    expect(result.current.participants.get('flock-council-member-b')?.persona).toBe(
      'council-member-b',
    );
    expect(result.current.participants.get('skuld-primary')).toMatchObject({
      persona: 'Skuld',
      participantType: 'skuld',
    });
  });

  it('hydrates assistant history with a synthesized Skuld participant when participant metadata is absent', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'conversation_history',
          turns: [
            {
              id: 'turn-2',
              role: 'assistant',
              content: 'I can summarize the current state for you.',
              created_at: '2026-05-01T10:20:00.000000+00:00',
            },
          ],
        }),
      );
    });

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(result.current.messages[0]?.participant).toMatchObject({
      peerId: 'skuld-primary',
      persona: 'Skuld',
      participantType: 'skuld',
    });
    expect(result.current.participants.get('skuld-primary')).toMatchObject({
      persona: 'Skuld',
    });
  });

  it('hydrates mesh outcome events from conversation_history outcome turns', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'conversation_history',
          turns: [
            {
              id: 'turn-1',
              role: 'assistant',
              content: `---outcome---
verdict: opinion_submitted
summary: Wrote opinion memo
page_path: council/demo/opinion-b.md
---end---`,
              created_at: '2026-05-01T10:18:36.889091+00:00',
              participant_id: 'flock-council-member-b',
              participant_meta: {
                peer_id: 'flock-council-member-b',
                persona: 'council-member-b',
                participant_type: 'ravn',
                display_name: 'council-member-b',
              },
            },
          ],
        }),
      );
    });

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(result.current.meshEvents).toHaveLength(1);
    expect(result.current.meshEvents[0]).toMatchObject({
      type: 'outcome',
      participantId: 'flock-council-member-b',
      persona: 'council-member-b',
      summary: 'Wrote opinion memo',
    });
  });

  it('normalizes punctuation spacing in history-derived outcome summaries', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'conversation_history',
          turns: [
            {
              id: 'turn-2',
              role: 'assistant',
              content: `---outcome---
verdict: opinion_submitted
summary: Wrote opinion recommending low-confidence, high-risk, or bootstrap/test cases.
page_path: council/demo/opinion-b.md
---end---`,
              created_at: '2026-05-01T10:19:36.889091+00:00',
              participant_id: 'flock-council-member-b',
              participant_meta: {
                peer_id: 'flock-council-member-b',
                persona: 'council-member-b',
                participant_type: 'ravn',
                display_name: 'council-member-b',
              },
            },
          ],
        }),
      );
    });

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));
    expect(result.current.meshEvents[0]).toMatchObject({
      summary: 'Wrote opinion recommending low-confidence, high-risk, or bootstrap/test cases.',
    });
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

  it('sends directed messages, permission responses, and clears state', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    await act(async () => {
      result.current.sendDirectedMessages(
        [{ peerId: 'flock-council-chair', persona: 'council-chair', displayName: 'chair' }],
        'Please revise the conclusion.',
        [],
      );
    });

    expect(sendJson).toHaveBeenCalledWith({
      type: 'directed_message',
      targetPeerId: 'flock-council-chair',
      content: 'Please revise the conclusion.',
    });
    expect(result.current.messages.at(-1)).toMatchObject({
      role: 'user',
      content: 'Please revise the conclusion.',
    });

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'control_request',
          request_id: 'perm-1',
          tool: 'Bash',
          input: { command: './start-dev' },
        }),
      );
    });

    expect(result.current.pendingPermissions).toHaveLength(1);
    expect(result.current.pendingPermissions[0]).toMatchObject({
      requestId: 'perm-1',
      toolName: 'Bash',
      description: './start-dev',
      command: './start-dev',
      input: { command: './start-dev' },
    });

    await act(async () => {
      result.current.respondToPermission('perm-1', 'allow_always');
    });

    expect(sendJson).toHaveBeenCalledWith({
      type: 'permission_response',
      request_id: 'perm-1',
      behavior: 'allowForever',
      updated_input: {},
      updated_permissions: [],
    });
    expect(result.current.pendingPermissions).toHaveLength(0);

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_notification',
          participantId: 'flock-council-chair',
          participant: {
            peer_id: 'flock-council-chair',
            persona: 'council-chair',
            participant_type: 'ravn',
          },
          notificationType: 'help_needed',
          summary: 'Need operator input',
        }),
      );
    });

    expect(result.current.meshEvents).toHaveLength(1);

    act(() => {
      result.current.clearMessages();
    });

    expect(result.current.messages).toHaveLength(0);
    expect(result.current.meshEvents).toHaveLength(0);
    expect(result.current.pendingPermissions).toHaveLength(0);
  });

  it('removes pending permissions when the broker resolves them', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'control_request',
          request_id: 'perm-broker',
          tool: 'Bash',
          input: { command: './stop-dev' },
        }),
      );
    });
    expect(result.current.pendingPermissions).toHaveLength(1);

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'permission_resolved',
          request_id: 'perm-broker',
          behavior: 'allow',
          auto_approved: true,
        }),
      );
    });

    expect(result.current.pendingPermissions).toHaveLength(0);
  });

  it('ignores blank directed replies and covers alternate permission behaviors', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    await act(async () => {
      result.current.sendDirectedMessages([], 'Please revise the conclusion.', []);
      result.current.sendDirectedMessages(
        [{ peerId: 'flock-council-chair', persona: 'council-chair', displayName: 'chair' }],
        '   ',
        [],
      );
    });
    expect(sendJson).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: 'directed_message' }),
    );

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'control_request',
          request_id: 'perm-allow-once',
          tool: 'web_fetch',
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'control_request',
          request_id: 'perm-deny',
          tool: 'bash',
        }),
      );
    });

    await act(async () => {
      result.current.respondToPermission('perm-allow-once', 'allow_once');
      result.current.respondToPermission('perm-deny', 'deny');
    });

    expect(sendJson).toHaveBeenCalledWith({
      type: 'permission_response',
      request_id: 'perm-allow-once',
      behavior: 'allow',
      updated_input: {},
      updated_permissions: [],
    });
    expect(sendJson).toHaveBeenCalledWith({
      type: 'permission_response',
      request_id: 'perm-deny',
      behavior: 'deny',
      updated_input: {},
      updated_permissions: [],
    });
  });

  it('sends plain and attachment-backed user messages through the websocket', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    await act(async () => {
      result.current.sendMessage('   ', []);
    });
    expect(sendJson).not.toHaveBeenCalled();

    await act(async () => {
      result.current.sendMessage('Need a summary', []);
    });

    await waitFor(() => {
      expect(sendJson).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'user',
          content: 'Need a summary',
          request_id: expect.any(String),
        }),
      );
    });
    expect(result.current.messages.at(-1)).toMatchObject({
      role: 'user',
      content: 'Need a summary',
    });
    const plainRequestId = sendJson.mock.calls.at(-1)?.[0]?.request_id as string;

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'user_confirmed',
          id: 'server-user-1',
          request_id: plainRequestId,
          content: 'Need a summary',
        }),
      );
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]).toMatchObject({
      id: 'server-user-1',
      role: 'user',
      content: 'Need a summary',
    });

    const image = new File(['png-data'], 'diagram.png', { type: 'image/png' });
    const compressedImage = Object.assign(new Blob(['png-data'], { type: 'image/png' }), {
      arrayBuffer: async () => new TextEncoder().encode('png-data').buffer,
    });
    await act(async () => {
      result.current.sendMessage('See attached', [
        {
          id: 'att-1',
          file: image,
          name: 'diagram.png',
          compressed: compressedImage,
          previewUrl: null,
        },
      ]);
    });

    await waitFor(() =>
      expect(sendJson).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'user',
          request_id: expect.any(String),
          content: [
            { type: 'text', text: 'See attached' },
            {
              type: 'image',
              source: {
                type: 'base64',
                media_type: 'image/png',
                data: 'cG5nLWRhdGE=',
              },
            },
          ],
        }),
      ),
    );
    expect(result.current.messages.at(-1)?.attachments).toEqual([
      {
        name: 'diagram.png',
        type: 'image',
        size: 8,
        contentType: 'image/png',
      },
    ]);
    const attachmentRequestId = sendJson.mock.calls.at(-1)?.[0]?.request_id as string;

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'user_confirmed',
          id: 'server-user-2',
          request_id: attachmentRequestId,
          content: JSON.stringify([
            { type: 'text', text: 'See attached' },
            {
              type: 'image',
              source: {
                type: 'base64',
                media_type: 'image/png',
                data: 'cG5nLWRhdGE=',
              },
            },
          ]),
        }),
      );
    });

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages.at(-1)).toMatchObject({
      id: 'server-user-2',
      role: 'user',
      content: 'See attached',
      attachments: [
        {
          name: 'diagram.png',
          type: 'image',
          size: 8,
          contentType: 'image/png',
        },
      ],
    });

    await act(async () => {
      result.current.sendMessage('   ', [
        {
          id: 'att-2',
          file: image,
          name: 'diagram.png',
          compressed: compressedImage,
          previewUrl: null,
        },
      ]);
    });

    await waitFor(() =>
      expect(sendJson).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'user',
          request_id: expect.any(String),
          content: [
            {
              type: 'image',
              source: {
                type: 'base64',
                media_type: 'image/png',
                data: 'cG5nLWRhdGE=',
              },
            },
          ],
        }),
      ),
    );

    const textFile = new File(['hello'], 'notes.txt', { type: 'text/plain' });
    await act(async () => {
      result.current.sendMessage('Ignore non-image attachments', [
        {
          id: 'att-3',
          file: textFile,
          name: 'notes.txt',
          compressed: null,
          previewUrl: null,
        },
      ]);
    });

    await waitFor(() =>
      expect(sendJson).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'user',
          request_id: expect.any(String),
          content: 'Ignore non-image attachments',
        }),
      ),
    );
  });

  it('emits the remaining control websocket commands', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      result.current.sendInterrupt();
      result.current.sendSetModel('gpt-5.5');
      result.current.sendSetThinkingTokens(2048);
      result.current.sendRewindFiles();
      result.current.sendSetInternalVisibility(true);
    });

    expect(sendJson).toHaveBeenCalledWith({ type: 'interrupt' });
    expect(sendJson).toHaveBeenCalledWith({ type: 'set_model', model: 'gpt-5.5' });
    expect(sendJson).toHaveBeenCalledWith({
      type: 'set_max_thinking_tokens',
      max_thinking_tokens: 2048,
    });
    expect(sendJson).toHaveBeenCalledWith({ type: 'rewind_files' });
    expect(sendJson).toHaveBeenCalledWith({ type: 'set_internal_visibility', visible: true });
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

  it('falls back to outcome field summaries and preserves invalid verdict markers', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_outcome',
          participant_id: 'peer-1',
          participant: { peer_id: 'peer-1', persona: 'reviewer', participant_type: 'ravn' },
          persona: 'reviewer',
          eventType: 'review.completed',
          verdict: 'conditional',
          valid: false,
          fields: {
            summary: 'Fallback summary from fields.',
          },
        }),
      );
    });

    expect(result.current.meshEvents[0]).toMatchObject({
      participantId: 'peer-1',
      summary: 'Fallback summary from fields.',
      valid: false,
    });
    expect(result.current.messages[0]?.content).toContain('summary: Fallback summary from fields.');
  });

  it('captures room mesh message review packets', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_mesh_message',
          participant_id: 'peer-chair',
          participant: {
            peer_id: 'peer-chair',
            persona: 'council-chair',
            participant_type: 'ravn',
            color: 'brand',
          },
          fromPersona: 'council-chair',
          eventType: 'research.review.packet',
          preview: 'Opinion B is ready for anonymous review.',
        }),
      );
    });

    expect(result.current.meshEvents[0]).toMatchObject({
      type: 'mesh_message',
      participantId: 'peer-chair',
      fromPersona: 'council-chair',
      eventType: 'research.review.packet',
      preview: 'Opinion B is ready for anonymous review.',
    });
  });

  it('normalizes punctuation spacing in live room_outcome summaries', async () => {
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
          summary: 'Wrote opinion for low-confidence,high-risk,or bootstrap/test cases.',
          fields: {
            verdict: 'needs_changes',
            summary: 'Wrote opinion for low-confidence,high-risk,or bootstrap/test cases.',
          },
        }),
      );
    });

    expect(result.current.meshEvents).toHaveLength(1);
    expect(result.current.meshEvents[0]).toMatchObject({
      summary: 'Wrote opinion for low-confidence, high-risk, or bootstrap/test cases.',
    });
    expect(result.current.messages[0]?.content).toContain(
      'summary: Wrote opinion for low-confidence, high-risk, or bootstrap/test cases.',
    );
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

  it('finalizes a participant stream before appending a room message from that participant', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_state',
          participants: [{ peer_id: 'peer-5', persona: 'reviewer', participant_type: 'ravn' }],
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_agent_event',
          participantId: 'peer-5',
          frame: {
            type: 'thought',
            data: 'Drafting the final review summary.',
          },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_message',
          participantId: 'peer-5',
          role: 'assistant',
          content: 'Final review: looks good.',
          participant: {
            peer_id: 'peer-5',
            persona: 'reviewer',
            participant_type: 'ravn',
          },
        }),
      );
    });

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[0]).toMatchObject({
      status: 'done',
      content: 'Drafting the final review summary.',
    });
    expect(result.current.messages[1]).toMatchObject({
      role: 'assistant',
      content: 'Final review: looks good.',
      participant: expect.objectContaining({ persona: 'reviewer' }),
    });
  });

  it('finalizes participant streams when room activity returns to idle', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_state',
          participants: [{ peer_id: 'peer-4', persona: 'reviewer', participant_type: 'ravn' }],
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_agent_event',
          participantId: 'peer-4',
          frame: {
            type: 'thought',
            data: 'Comparing opinions before final verdict.',
          },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_activity',
          participantId: 'peer-4',
          activityType: 'idle',
        }),
      );
    });

    expect(result.current.participants.get('peer-4')?.status).toBe('idle');
    expect(result.current.messages.at(-1)).toMatchObject({
      status: 'done',
      content: 'Comparing opinions before final verdict.',
      parts: [{ type: 'reasoning', text: 'Comparing opinions before final verdict.' }],
    });
  });

  it('streams room agent tool frames, text frames, and finalizes on socket close', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onOpen?.();
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_state',
          participants: [
            { peer_id: 'peer-2', persona: 'council-member-b', participant_type: 'ravn' },
          ],
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_agent_event',
          participantId: 'peer-2',
          frame: {
            type: 'tool_start',
            data: 'mimir_write',
            metadata: { input: { path: 'council/demo.md' } },
          },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_agent_event',
          participantId: 'peer-2',
          frame: {
            type: 'tool_result',
            data: { ok: true },
          },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_agent_event',
          participantId: 'peer-2',
          frame: {
            type: 'text',
            data: { summary: 'Drafted opinion memo.' },
          },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_agent_event',
          participantId: 'peer-2',
          frame: {
            type: 'other',
            data: 'ignored',
          },
        }),
      );
    });

    expect(result.current.connected).toBe(true);
    expect(result.current.messages.at(-1)?.parts).toEqual([
      {
        type: 'tool_use',
        id: expect.any(String),
        name: 'mimir_write',
        input: { path: 'council/demo.md' },
      },
      {
        type: 'tool_result',
        tool_use_id: expect.any(String),
        content: '{"ok":true}',
      },
      {
        type: 'text',
        text: '{"summary":"Drafted opinion memo."}',
      },
    ]);

    act(() => {
      wsHandlers.onClose?.();
    });

    expect(result.current.connected).toBe(false);
    expect(result.current.messages.at(-1)?.status).toBe('running');
  });

  it('falls back to empty tool input and ignores unknown websocket event types', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    await act(async () => {
      result.current.sendMessage('Seed context', []);
    });

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_state',
          participants: [{ peer_id: 'peer-3', persona: 'chair', participant_type: 'ravn' }],
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_agent_event',
          participantId: 'peer-3',
          frame: {
            type: 'tool_start',
            data: 'mimir_search',
            metadata: { input: 'not-an-object' },
          },
        }),
      );
      wsHandlers.onMessage?.(JSON.stringify({ type: 'mystery_event', payload: 'ignored' }));
    });

    expect(result.current.messages.at(-1)?.parts).toEqual([
      {
        type: 'tool_use',
        id: expect.any(String),
        name: 'mimir_search',
        input: {},
      },
    ]);
    expect(result.current.messages[0]?.content).toBe('Seed context');
  });

  it('marks the socket disconnected on websocket errors', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onOpen?.();
    });
    expect(result.current.connected).toBe(true);

    act(() => {
      wsHandlers.onError?.();
    });
    expect(result.current.connected).toBe(false);
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

  it('streams assistant reasoning and text parts, token usage, capabilities, and system events', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'assistant',
          message: {
            model: 'claude-sonnet-4-6',
            usage: { input_tokens: 111 },
            content: [],
          },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'content_block_start',
          content_block: { type: 'thinking' },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'content_block_delta',
          delta: { type: 'thinking_delta', thinking: 'First, inspect the diff. ' },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'content_block_start',
          content_block: { type: 'text' },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'content_block_delta',
          delta: { type: 'text_delta', text: 'Summary ready.' },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'message_delta',
          usage: { output_tokens: 222 },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'capabilities',
          interrupt: true,
          set_model: true,
          set_thinking_tokens: true,
          rewind_files: true,
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'result',
          result: 'Summary ready.',
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'system',
          subtype: 'init',
          message: { model: 'claude-sonnet-4-6' },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'system',
          content: 'Worker joined the room.',
        }),
      );
      wsHandlers.onMessage?.(JSON.stringify({ type: 'system', subtype: 'heartbeat' }));
    });

    expect(result.current.messages[0]).toMatchObject({
      role: 'assistant',
      content: 'Summary ready.',
      status: 'done',
      parts: [
        { type: 'reasoning', text: 'First, inspect the diff. ' },
        { type: 'text', text: 'Summary ready.' },
      ],
      metadata: {
        usage: {
          'claude-sonnet-4-6': {
            inputTokens: 111,
            outputTokens: 222,
          },
        },
      },
    });
    expect(result.current.capabilities).toEqual({
      interrupt: true,
      set_model: true,
      set_thinking_tokens: true,
      rewind_files: true,
      slash_commands: false,
      terminal_input: false,
      terminal_keys: false,
      terminal_output: false,
      terminal_panes: false,
      terminal_resize: false,
    });
    expect(result.current.messages.slice(1).map((message) => message.content)).toEqual([
      'Session initialized · claude-sonnet-4-6',
      'Worker joined the room.',
      'System event',
    ]);
  });

  it('builds fallback reasoning and text parts before finalizing an anonymous stream', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'content_block_delta',
          delta: { type: 'thinking_delta', thinking: 'Inspecting edge cases.' },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'content_block_delta',
          delta: { text: 'Final answer.' },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'result',
          result: 'Final answer.',
        }),
      );
    });

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]).toMatchObject({
      role: 'assistant',
      content: 'Final answer.',
      status: 'done',
      parts: [
        { type: 'reasoning', text: 'Inspecting edge cases.' },
        { type: 'text', text: 'Final answer.' },
      ],
    });
  });

  it('keeps empty tool input on invalid json deltas and finalizes assistant errors', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'assistant',
          message: { model: 'claude-sonnet-4-6', content: [] },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'content_block_start',
          content_block: {
            type: 'tool_use',
            id: 'tool-bad-json',
            name: 'Read',
          },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'content_block_delta',
          delta: { type: 'input_json_delta', partial_json: '{"file_path":"/tmp/demo.ts"' },
        }),
      );
      wsHandlers.onMessage?.(JSON.stringify({ type: 'content_block_stop' }));
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'error',
          error: { message: 'Tool stream failed.' },
        }),
      );
    });

    expect(result.current.messages.at(-1)).toMatchObject({
      status: 'error',
      content: 'Tool stream failed.',
      parts: [
        {
          type: 'tool_use',
          id: 'tool-bad-json',
          name: 'Read',
          input: {},
        },
      ],
    });
  });

  it('drops stale empty assistant streams when the websocket closes', async () => {
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
    expect(result.current.messages).toHaveLength(1);

    act(() => {
      wsHandlers.onClose?.();
    });

    expect(result.current.messages).toHaveLength(0);
    expect(result.current.connected).toBe(false);
  });

  it('uses file paths when broker permission requests do not include commands', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'control_request',
          request_id: 'perm-file',
          tool: 'Read',
          input: { file_path: '/tmp/review.txt' },
        }),
      );
    });

    expect(result.current.pendingPermissions[0]).toMatchObject({
      requestId: 'perm-file',
      toolName: 'Read',
      description: '/tmp/review.txt',
      command: undefined,
    });
  });

  it('covers control and participant guard branches without mutating state', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'control_request',
          input: { command: 'missing-request-id' },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'control_request',
          request_id: 'perm-path',
          input: { path: '/tmp/from-path.txt' },
        }),
      );
      wsHandlers.onMessage?.(JSON.stringify({ type: 'permission_resolved' }));
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'participant_joined',
          participant: { persona: 'missing-peer' },
        }),
      );
      wsHandlers.onMessage?.(JSON.stringify({ type: 'participant_left' }));
    });

    expect(result.current.pendingPermissions).toEqual([
      expect.objectContaining({
        requestId: 'perm-path',
        toolName: 'unknown',
        description: '/tmp/from-path.txt',
      }),
    ]);
    expect(result.current.participants.size).toBe(0);
  });

  it('covers room event fallback payloads and guard clauses', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_state',
          participants: [{ persona: 'missing-peer' }],
        }),
      );
      wsHandlers.onMessage?.(JSON.stringify({ type: 'room_activity' }));
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_message',
          participant_id: 'peer-user',
          role: 'user',
          content: 'Operator reply',
          participant: { peer_id: 'peer-user', persona: 'operator', participant_type: 'ravn' },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_message',
          content: ['not-a-string'],
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_outcome',
          fields: {},
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_mesh_message',
          participant: {},
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_notification',
          participant_id: 'peer-note',
          participant: {},
        }),
      );
    });

    expect(result.current.participants.size).toBe(0);
    expect(result.current.messages[0]).toMatchObject({
      role: 'user',
      content: 'Operator reply',
      participant: expect.objectContaining({ persona: 'operator' }),
    });
    expect(result.current.messages[1]).toMatchObject({
      role: 'assistant',
      content: '',
    });
    expect(result.current.meshEvents).toEqual([
      expect.objectContaining({
        type: 'outcome',
        participantId: '',
        persona: '',
        eventType: '',
      }),
      expect.objectContaining({
        type: 'mesh_message',
        participantId: '',
        fromPersona: '',
        eventType: '',
      }),
      expect.objectContaining({
        type: 'notification',
        participantId: 'peer-note',
        persona: '',
        notificationType: '',
        summary: '',
        urgency: 0.5,
      }),
    ]);
  });

  it('appends a tool_result part paired with its tool_use by id', async () => {
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

    // Open + close a tool_use block
    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'content_block_start',
          content_block: { type: 'tool_use', id: 'cmd-1', name: 'Bash' },
        }),
      );
    });
    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'content_block_delta',
          delta: { type: 'input_json_delta', partial_json: '{"command":"ls"}' },
        }),
      );
    });
    act(() => {
      wsHandlers.onMessage?.(JSON.stringify({ type: 'content_block_stop' }));
    });

    // Then the matching tool_result block carrying the output
    act(() => {
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'content_block_start',
          content_block: {
            type: 'tool_result',
            tool_use_id: 'cmd-1',
            content: 'README.md',
          },
        }),
      );
    });
    act(() => {
      wsHandlers.onMessage?.(JSON.stringify({ type: 'content_block_stop' }));
    });

    const parts = result.current.messages.at(-1)?.parts ?? [];
    expect(parts).toEqual([
      { type: 'tool_use', id: 'cmd-1', name: 'Bash', input: { command: 'ls' } },
      { type: 'tool_result', tool_use_id: 'cmd-1', content: 'README.md' },
    ]);
  });

  it('sendSetInternalVisibility forwards the toggle to the backend', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      result.current.sendSetInternalVisibility(true);
    });
    act(() => {
      result.current.sendSetInternalVisibility(false);
    });

    expect(sendJson).toHaveBeenCalledWith({ type: 'set_internal_visibility', visible: true });
    expect(sendJson).toHaveBeenCalledWith({ type: 'set_internal_visibility', visible: false });
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

  it('covers room_agent_event fallback branches for missing ids, empty data, and generated tool ids', async () => {
    const { result } = renderHook(() => useSkuldChat('ws://localhost:8080/s/test/session'));

    await waitFor(() => expect(result.current.historyLoaded).toBe(true));

    act(() => {
      wsHandlers.onMessage?.(JSON.stringify({ type: 'room_agent_event' }));
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_state',
          participants: [
            { peer_id: 'peer-fallback', persona: 'reviewer', participant_type: 'ravn' },
            { peer_id: 'peer-generated', persona: 'observer', participant_type: 'ravn' },
          ],
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_agent_event',
          participant_id: 'peer-fallback',
          frame: { type: 'thought' },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_agent_event',
          participantId: 'peer-fallback',
          frame: {
            type: 'tool_start',
            data: { ignored: true },
            metadata: { tool_name: 'Read', input: null },
          },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_agent_event',
          participantId: 'peer-fallback',
          frame: {
            type: 'tool_result',
            data: 'done',
          },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_agent_event',
          participantId: 'peer-fallback',
          frame: {
            type: 'text',
            data: 'All set.',
          },
        }),
      );
      wsHandlers.onMessage?.(
        JSON.stringify({
          type: 'room_agent_event',
          participantId: 'peer-generated',
          frame: {
            type: 'tool_result',
            data: 'orphan result',
          },
        }),
      );
    });

    expect(result.current.agentEvents.get('peer-fallback')).toHaveLength(4);
    expect(result.current.messages[0]).toMatchObject({
      role: 'assistant',
      participantId: 'peer-fallback',
      content: '""All set.',
      parts: [
        { type: 'reasoning', text: '""' },
        { type: 'tool_use', id: expect.any(String), name: 'Read', input: {} },
        { type: 'tool_result', tool_use_id: expect.any(String), content: 'done' },
        { type: 'text', text: 'All set.' },
      ],
    });
    const toolUsePart = result.current.messages[0]?.parts?.[1];
    const toolResultPart = result.current.messages[0]?.parts?.[2];
    expect(toolUsePart?.type).toBe('tool_use');
    expect(toolResultPart?.type).toBe('tool_result');
    expect(toolResultPart?.tool_use_id).toBe((toolUsePart as { id: string }).id);
    expect(result.current.messages[1]).toMatchObject({
      participantId: 'peer-generated',
      content: '',
      parts: [
        {
          type: 'tool_result',
          tool_use_id: expect.stringMatching(/^tool-/),
          content: 'orphan result',
        },
      ],
    });
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
      getStorageKey('ws://localhost:8080/s/test/session'),
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
