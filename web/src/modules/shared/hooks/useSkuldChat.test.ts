import { renderHook, act } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { useSkuldChat, DEFAULT_CAPABILITIES } from './useSkuldChat';

// Mock the useWebSocket hook
vi.mock('@/hooks/useWebSocket', () => ({
  useWebSocket: vi.fn(),
}));

// Mock the chat store
vi.mock('@/modules/shared/store/chat.store', () => ({
  useChatStore: vi.fn(() => ({
    getMessages: vi.fn(() => []),
    setMessages: vi.fn(),
    getMeshEvents: vi.fn(() => []),
    setMeshEvents: vi.fn(),
    clearSession: vi.fn(),
  })),
}));

// Mock getAccessToken so we can test Authorization header branch
vi.mock('@/modules/shared/api/client', () => ({
  getAccessToken: vi.fn(() => null),
}));

import { useWebSocket } from '@/hooks/useWebSocket';
import { useChatStore } from '@/modules/shared/store/chat.store';
import { getAccessToken } from '@/modules/shared/api/client';

type MessageHandler = (raw: string) => void;
type OpenHandler = () => void;
type CloseHandler = (code: number, reason: string) => void;
type ErrorHandler = (event: Event) => void;

interface CapturedHandlers {
  onOpen?: OpenHandler;
  onMessage?: MessageHandler;
  onClose?: CloseHandler;
  onError?: ErrorHandler;
}

function setupMock() {
  const handlers: CapturedHandlers = {};
  const sendJson = vi.fn();
  const storeMock = {
    getMessages: vi.fn(() => []),
    setMessages: vi.fn(),
    getMeshEvents: vi.fn(() => []),
    setMeshEvents: vi.fn(),
    clearSession: vi.fn(),
  };

  vi.mocked(useChatStore).mockReturnValue(storeMock);

  vi.mocked(useWebSocket).mockImplementation((_url, options = {}) => {
    handlers.onOpen = options.onOpen;
    handlers.onMessage = options.onMessage;
    handlers.onClose = options.onClose;
    handlers.onError = options.onError;
    return {
      send: vi.fn(),
      sendJson,
      close: vi.fn(),
      getSocket: vi.fn(() => null),
    };
  });

  return { handlers, sendJson, storeMock };
}

// ── Helpers to build Claude CLI stream-json events ──────────

function assistantEvent(model = 'claude-sonnet-4-5-20250514', inputTokens = 100) {
  return JSON.stringify({
    type: 'assistant',
    message: {
      id: 'msg_test',
      role: 'assistant',
      model,
      content: [],
      usage: { input_tokens: inputTokens, output_tokens: 0 },
    },
  });
}

function contentBlockDelta(text: string) {
  return JSON.stringify({
    type: 'content_block_delta',
    index: 0,
    delta: { type: 'text_delta', text },
  });
}

function messageDelta(outputTokens: number) {
  return JSON.stringify({
    type: 'message_delta',
    delta: { stop_reason: 'end_turn' },
    usage: { output_tokens: outputTokens },
  });
}

function resultEvent(
  opts: {
    totalCostUsd?: number;
    numTurns?: number;
    isError?: boolean;
  } = {}
) {
  return JSON.stringify({
    type: 'result',
    subtype: opts.isError ? 'error' : 'success',
    is_error: opts.isError ?? false,
    total_cost_usd: opts.totalCostUsd ?? 0.05,
    num_turns: opts.numTurns ?? 1,
    duration_ms: 1000,
    session_id: 'session-test',
  });
}

function errorEvent(message: string) {
  return JSON.stringify({
    type: 'error',
    error: message,
  });
}

function contentBlockStart(index: number, blockType: 'text' | 'thinking') {
  return JSON.stringify({
    type: 'content_block_start',
    index,
    content_block: { type: blockType, text: '' },
  });
}

function thinkingDelta(thinking: string) {
  return JSON.stringify({
    type: 'content_block_delta',
    index: 0,
    delta: { type: 'thinking_delta', thinking },
  });
}

describe('useSkuldChat', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('starts disconnected with empty messages', () => {
    setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    expect(result.current.connected).toBe(false);
    expect(result.current.isRunning).toBe(false);
    expect(result.current.messages).toEqual([]);
  });

  it('sets connected to true when WebSocket opens', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());

    expect(result.current.connected).toBe(true);
  });

  it('sets connected to false when WebSocket closes', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    expect(result.current.connected).toBe(true);

    act(() => handlers.onClose?.(1000, 'normal'));
    expect(result.current.connected).toBe(false);
  });

  it('sends a user message and transitions to running', () => {
    const { handlers, sendJson } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('hello'));

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].role).toBe('user');
    expect(result.current.messages[0].content).toBe('hello');
    expect(result.current.messages[0].status).toBe('complete');
    expect(result.current.isRunning).toBe(true);
    expect(sendJson).toHaveBeenCalledWith({ type: 'user', content: 'hello' });
  });

  it('ignores send when disconnected', () => {
    const { sendJson } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => result.current.sendMessage('hello'));

    expect(result.current.messages).toHaveLength(0);
    expect(sendJson).not.toHaveBeenCalled();
  });

  it('allows send even when already running', () => {
    const { handlers, sendJson } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('first'));
    expect(sendJson).toHaveBeenCalledTimes(1);

    act(() => result.current.sendMessage('second'));
    expect(sendJson).toHaveBeenCalledTimes(2);
    expect(result.current.messages).toHaveLength(2);
  });

  it('ignores empty/whitespace messages', () => {
    const { handlers, sendJson } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('   '));

    expect(result.current.messages).toHaveLength(0);
    expect(sendJson).not.toHaveBeenCalled();
  });

  it('creates assistant message on assistant event and accumulates text deltas', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));

    // assistant event starts a new message
    act(() => handlers.onMessage?.(assistantEvent()));

    expect(result.current.messages).toHaveLength(2); // user + assistant
    expect(result.current.messages[1].role).toBe('assistant');
    expect(result.current.messages[1].content).toBe('');
    expect(result.current.messages[1].status).toBe('running');

    // text deltas accumulate
    act(() => handlers.onMessage?.(contentBlockDelta('Hello')));
    expect(result.current.messages[1].content).toBe('Hello');

    act(() => handlers.onMessage?.(contentBlockDelta(' world')));
    expect(result.current.messages[1].content).toBe('Hello world');
    expect(result.current.messages[1].status).toBe('running');
  });

  it('finalizes message on result event with metadata', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));

    act(() => handlers.onMessage?.(assistantEvent('claude-opus-4-5-20251101', 100)));
    act(() => handlers.onMessage?.(contentBlockDelta('Hi there')));
    act(() => handlers.onMessage?.(messageDelta(50)));
    act(() => handlers.onMessage?.(resultEvent({ totalCostUsd: 0.05, numTurns: 1 })));

    expect(result.current.messages).toHaveLength(2);
    const assistantMsg = result.current.messages[1];
    expect(assistantMsg.content).toBe('Hi there');
    expect(assistantMsg.status).toBe('complete');
    expect(assistantMsg.metadata?.cost).toBe(0.05);
    expect(assistantMsg.metadata?.turns).toBe(1);
    expect(assistantMsg.metadata?.usage).toEqual({
      'claude-opus-4-5-20251101': {
        inputTokens: 100,
        outputTokens: 50,
      },
    });
    expect(result.current.isRunning).toBe(false);
  });

  it('captures output tokens from message_delta', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));
    act(() => handlers.onMessage?.(assistantEvent('claude-sonnet-4-5-20250514', 200)));
    act(() => handlers.onMessage?.(contentBlockDelta('response')));
    act(() => handlers.onMessage?.(messageDelta(75)));
    act(() => handlers.onMessage?.(resultEvent()));

    const msg = result.current.messages[1];
    expect(msg.metadata?.usage?.['claude-sonnet-4-5-20250514']?.outputTokens).toBe(75);
    expect(msg.metadata?.usage?.['claude-sonnet-4-5-20250514']?.inputTokens).toBe(200);
  });

  it('handles error result (is_error: true)', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));
    act(() => handlers.onMessage?.(assistantEvent()));
    act(() => handlers.onMessage?.(contentBlockDelta('partial output')));
    act(() => handlers.onMessage?.(resultEvent({ isError: true, totalCostUsd: 0.01 })));

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1].status).toBe('error');
    expect(result.current.messages[1].content).toBe('partial output');
    expect(result.current.messages[1].metadata?.cost).toBe(0.01);
    expect(result.current.isRunning).toBe(false);
  });

  it('handles error result without prior streaming', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));
    act(() => handlers.onMessage?.(resultEvent({ isError: true })));

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1].status).toBe('error');
    expect(result.current.messages[1].content).toBe('An error occurred');
    expect(result.current.isRunning).toBe(false);
  });

  it('handles error events during streaming', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));
    act(() => handlers.onMessage?.(assistantEvent()));
    act(() => handlers.onMessage?.(contentBlockDelta('partial')));
    act(() => handlers.onMessage?.(errorEvent('Something went wrong')));

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1].content).toBe('Something went wrong');
    expect(result.current.messages[1].status).toBe('error');
    expect(result.current.isRunning).toBe(false);
  });

  it('handles error events without prior streaming', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));
    act(() => handlers.onMessage?.(errorEvent('Connection failed')));

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1].role).toBe('assistant');
    expect(result.current.messages[1].content).toBe('Connection failed');
    expect(result.current.messages[1].status).toBe('error');
    expect(result.current.isRunning).toBe(false);
  });

  it('handles error object with message field', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));
    act(() =>
      handlers.onMessage?.(JSON.stringify({ type: 'error', error: { message: 'Rate limited' } }))
    );

    expect(result.current.messages[1].content).toBe('Rate limited');
    expect(result.current.messages[1].status).toBe('error');
  });

  it('silently ignores init, content_block_start/stop, message_stop', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());

    act(() => handlers.onMessage?.(JSON.stringify({ type: 'init', session_id: 'sess-1' })));
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'content_block_start',
          index: 0,
          content_block: { type: 'text', text: '' },
        })
      )
    );
    act(() => handlers.onMessage?.(JSON.stringify({ type: 'content_block_stop', index: 0 })));
    act(() => handlers.onMessage?.(JSON.stringify({ type: 'message_stop' })));

    expect(result.current.messages).toHaveLength(0);
  });

  it('creates system message for hook_started event', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'system',
          subtype: 'hook_started',
          hook_name: 'SessionStart:resume',
          hook_event: 'SessionStart',
        })
      )
    );

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].role).toBe('assistant');
    expect(result.current.messages[0].content).toBe('Hook started: SessionStart:resume');
    expect(result.current.messages[0].status).toBe('complete');
    expect(result.current.messages[0].metadata?.messageType).toBe('system');
    expect(result.current.messages[0].metadata?.systemSubtype).toBe('hook_started');
  });

  it('creates system message for hook_response event', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'system',
          subtype: 'hook_response',
          hook_name: 'SessionStart:resume',
          output: 'Installing pre-commit...\nDone.',
          stderr: 'ERROR: permission denied',
        })
      )
    );

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].content).toBe(
      'Hook SessionStart:resume: Installing pre-commit... (errors)'
    );
    expect(result.current.messages[0].metadata?.messageType).toBe('system');
    expect(result.current.messages[0].metadata?.systemSubtype).toBe('hook_response');
  });

  it('creates system message for init event', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'system',
          subtype: 'init',
          model: 'claude-sonnet-4-5-20250929',
          tools: ['Bash', 'Read', 'Write', 'Glob', 'Grep'],
          session_id: 'sess-123',
        })
      )
    );

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].content).toBe(
      'Session initialized · claude-sonnet-4-5-20250929 · 5 tools'
    );
    expect(result.current.messages[0].metadata?.messageType).toBe('system');
    expect(result.current.messages[0].metadata?.systemSubtype).toBe('init');
  });

  it('creates system message for generic system event with content', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'system',
          content: 'Connected to session abc-123',
        })
      )
    );

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].content).toBe('Connected to session abc-123');
    expect(result.current.messages[0].metadata?.messageType).toBe('system');
    expect(result.current.messages[0].metadata?.systemSubtype).toBe('info');
  });

  it('creates system message with fallback content when no content field', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'system',
        })
      )
    );

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].content).toBe('System event');
  });

  it('ignores malformed JSON messages', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => handlers.onMessage?.('not json'));

    expect(result.current.messages).toHaveLength(0);
  });

  it('clearMessages resets all state', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('hello'));
    act(() => handlers.onMessage?.(assistantEvent()));
    act(() => handlers.onMessage?.(contentBlockDelta('hi')));

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.isRunning).toBe(true);

    act(() => result.current.clearMessages());

    expect(result.current.messages).toHaveLength(0);
    expect(result.current.isRunning).toBe(false);
  });

  it('resets streaming state on WebSocket close', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));
    act(() => handlers.onMessage?.(assistantEvent()));
    act(() => handlers.onMessage?.(contentBlockDelta('partial')));

    expect(result.current.isRunning).toBe(true);

    act(() => handlers.onClose?.(1006, 'abnormal'));

    expect(result.current.isRunning).toBe(false);
    expect(result.current.connected).toBe(false);
  });

  it('resets streaming state on WebSocket error', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));

    expect(result.current.isRunning).toBe(true);

    act(() => handlers.onError?.(new Event('error')));

    expect(result.current.isRunning).toBe(false);
    expect(result.current.connected).toBe(false);
  });

  it('calls onConnect and onDisconnect callbacks', () => {
    const { handlers } = setupMock();
    const onConnect = vi.fn();
    const onDisconnect = vi.fn();

    renderHook(() => useSkuldChat('wss://test/session', { onConnect, onDisconnect }));

    act(() => handlers.onOpen?.());
    expect(onConnect).toHaveBeenCalledTimes(1);

    act(() => handlers.onClose?.(1000, 'normal'));
    expect(onDisconnect).toHaveBeenCalledTimes(1);
  });

  it('handles full streaming conversation flow', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('What is 2+2?'));

    // Simulate full Claude CLI stream-json sequence
    act(() => handlers.onMessage?.(JSON.stringify({ type: 'init', session_id: 'sess-abc' })));
    act(() => handlers.onMessage?.(assistantEvent('claude-sonnet-4-5-20250514', 150)));
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'content_block_start',
          index: 0,
          content_block: { type: 'text', text: '' },
        })
      )
    );
    act(() => handlers.onMessage?.(contentBlockDelta('2 + 2')));
    act(() => handlers.onMessage?.(contentBlockDelta(' = 4')));
    act(() => handlers.onMessage?.(JSON.stringify({ type: 'content_block_stop', index: 0 })));
    act(() => handlers.onMessage?.(messageDelta(25)));
    act(() => handlers.onMessage?.(JSON.stringify({ type: 'message_stop' })));
    act(() => handlers.onMessage?.(resultEvent({ totalCostUsd: 0.003, numTurns: 1 })));

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[0].role).toBe('user');
    expect(result.current.messages[0].content).toBe('What is 2+2?');
    expect(result.current.messages[1].role).toBe('assistant');
    expect(result.current.messages[1].content).toBe('2 + 2 = 4');
    expect(result.current.messages[1].status).toBe('complete');
    expect(result.current.messages[1].metadata?.cost).toBe(0.003);
    expect(result.current.messages[1].metadata?.usage?.['claude-sonnet-4-5-20250514']).toEqual({
      inputTokens: 150,
      outputTokens: 25,
    });
    expect(result.current.isRunning).toBe(false);
  });

  it('handles multi-line NDJSON WebSocket frames', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));

    // Send multiple events in a single WebSocket frame (NDJSON)
    const multiLine = [
      assistantEvent(),
      contentBlockDelta('Hello'),
      contentBlockDelta(' there'),
    ].join('\n');

    act(() => handlers.onMessage?.(multiLine));

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1].role).toBe('assistant');
    expect(result.current.messages[1].content).toBe('Hello there');
  });

  it('strips SSE "data:" prefix from events', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));

    // SSE-formatted event
    act(() => handlers.onMessage?.(`data: ${assistantEvent()}`));
    act(() => handlers.onMessage?.(`data:${contentBlockDelta('SSE text')}`));
    act(() => handlers.onMessage?.(resultEvent()));

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1].content).toBe('SSE text');
    expect(result.current.messages[1].status).toBe('complete');
  });

  it('extracts initial text from assistant event message.content', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));

    // Assistant event with pre-populated content
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'assistant',
          message: {
            id: 'msg_test',
            role: 'assistant',
            model: 'claude-sonnet-4-5-20250514',
            content: [
              { type: 'text', text: 'Pre-filled ' },
              { type: 'text', text: 'content' },
            ],
            usage: { input_tokens: 50, output_tokens: 0 },
          },
        })
      )
    );

    expect(result.current.messages[1].content).toBe('Pre-filled content');

    // Further deltas append
    act(() => handlers.onMessage?.(contentBlockDelta(' plus more')));
    expect(result.current.messages[1].content).toBe('Pre-filled content plus more');
  });

  it('falls back to result.result string when no text was streamed', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));

    act(() => handlers.onMessage?.(assistantEvent()));
    // No content_block_delta events — text comes via result.result
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'result',
          subtype: 'success',
          is_error: false,
          result: 'Fallback result text',
          total_cost_usd: 0.01,
          num_turns: 1,
          duration_ms: 500,
          session_id: 'sess-test',
        })
      )
    );

    expect(result.current.messages[1].content).toBe('Fallback result text');
    expect(result.current.messages[1].status).toBe('complete');
  });

  it('falls back to result.content string when no text was streamed', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));

    act(() => handlers.onMessage?.(assistantEvent()));
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'result',
          subtype: 'success',
          is_error: false,
          content: 'Content string fallback',
          total_cost_usd: 0.02,
          num_turns: 1,
          duration_ms: 500,
          session_id: 'sess-test',
        })
      )
    );

    expect(result.current.messages[1].content).toBe('Content string fallback');
    expect(result.current.messages[1].status).toBe('complete');
  });

  it('falls back to result.content array when no text was streamed', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));

    act(() => handlers.onMessage?.(assistantEvent()));
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'result',
          subtype: 'success',
          is_error: false,
          content: [
            { type: 'text', text: 'Array ' },
            { type: 'text', text: 'content' },
          ],
          total_cost_usd: 0.03,
          num_turns: 1,
          duration_ms: 500,
          session_id: 'sess-test',
        })
      )
    );

    expect(result.current.messages[1].content).toBe('Array content');
    expect(result.current.messages[1].status).toBe('complete');
  });

  it('creates message from result without prior assistant event', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));

    // Result arrives without assistant/delta events, with content fallback
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'result',
          subtype: 'success',
          is_error: false,
          content: 'Direct result content',
          total_cost_usd: 0.01,
          num_turns: 1,
          duration_ms: 500,
          session_id: 'sess-test',
        })
      )
    );

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1].content).toBe('Direct result content');
    expect(result.current.messages[1].status).toBe('complete');
  });

  it('handles delta with direct text (no text_delta type)', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));

    act(() => handlers.onMessage?.(assistantEvent()));
    // Delta without type field, just direct text
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'content_block_delta',
          index: 0,
          delta: { text: 'direct text' },
        })
      )
    );

    expect(result.current.messages[1].content).toBe('direct text');
  });

  it('handles unknown error object shape', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));

    // Error with neither string nor object.message
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'error',
          error: { code: 500 },
        })
      )
    );

    expect(result.current.messages[1].content).toBe('Unknown error');
    expect(result.current.messages[1].status).toBe('error');
  });

  it('prefers streamed text over result fallback text', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));

    act(() => handlers.onMessage?.(assistantEvent()));
    act(() => handlers.onMessage?.(contentBlockDelta('Streamed text')));
    // Result also has content field, but streamed text should win
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'result',
          subtype: 'success',
          is_error: false,
          content: 'Result fallback',
          total_cost_usd: 0.01,
          num_turns: 1,
          duration_ms: 500,
          session_id: 'sess-test',
        })
      )
    );

    expect(result.current.messages[1].content).toBe('Streamed text');
  });

  it('restores messages from store on mount', () => {
    const { storeMock } = setupMock();
    const storedMessages = [
      {
        id: 'restored-1',
        role: 'user' as const,
        content: 'Previously sent',
        createdAt: new Date('2025-06-01T12:00:00Z'),
        status: 'complete' as const,
      },
    ];
    storeMock.getMessages.mockReturnValue(storedMessages);

    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].id).toBe('restored-1');
    expect(result.current.messages[0].content).toBe('Previously sent');
  });

  it('persists messages to store when they change', () => {
    const { handlers, storeMock } = setupMock();
    renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());

    // persistMessages should have been called with the initial (empty) messages
    expect(storeMock.setMessages).toHaveBeenCalledWith('wss://test/session', []);
  });

  it('clearMessages also clears the store session', () => {
    const { handlers, storeMock } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));
    act(() => result.current.clearMessages());

    expect(storeMock.clearSession).toHaveBeenCalledWith('wss://test/session');
  });

  it('does not restore messages when url is null', () => {
    const { storeMock } = setupMock();
    const { result } = renderHook(() => useSkuldChat(null));

    expect(storeMock.getMessages).not.toHaveBeenCalled();
    expect(result.current.messages).toEqual([]);
  });

  describe('thinking/reasoning blocks', () => {
    it('tracks reasoning parts from thinking content blocks', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => result.current.sendMessage('test'));
      act(() => handlers.onMessage?.(assistantEvent()));
      act(() => handlers.onMessage?.(contentBlockStart(0, 'thinking')));
      act(() => handlers.onMessage?.(thinkingDelta('Let me think')));
      act(() => handlers.onMessage?.(thinkingDelta(' about this...')));

      const msg = result.current.messages[1];
      expect(msg.parts).toHaveLength(1);
      expect(msg.parts![0]).toEqual({ type: 'reasoning', text: 'Let me think about this...' });
    });

    it('tracks text parts from content_block_start', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => result.current.sendMessage('test'));
      act(() => handlers.onMessage?.(assistantEvent()));
      act(() => handlers.onMessage?.(contentBlockStart(0, 'text')));
      act(() => handlers.onMessage?.(contentBlockDelta('Hello')));

      const msg = result.current.messages[1];
      expect(msg.parts).toHaveLength(1);
      expect(msg.parts![0]).toEqual({ type: 'text', text: 'Hello' });
    });

    it('handles full thinking → text flow with parts in final message', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => result.current.sendMessage('What is 2+2?'));

      act(() => handlers.onMessage?.(assistantEvent('claude-sonnet-4-5-20250514', 100)));
      // Thinking block
      act(() => handlers.onMessage?.(contentBlockStart(0, 'thinking')));
      act(() => handlers.onMessage?.(thinkingDelta('The user wants simple arithmetic.')));
      // Text block
      act(() => handlers.onMessage?.(contentBlockStart(1, 'text')));
      act(() => handlers.onMessage?.(contentBlockDelta('2 + 2 = 4')));
      act(() => handlers.onMessage?.(messageDelta(30)));
      act(() => handlers.onMessage?.(resultEvent({ totalCostUsd: 0.01, numTurns: 1 })));

      const msg = result.current.messages[1];
      expect(msg.status).toBe('complete');
      expect(msg.content).toBe('2 + 2 = 4');
      expect(msg.parts).toHaveLength(2);
      expect(msg.parts![0]).toEqual({
        type: 'reasoning',
        text: 'The user wants simple arithmetic.',
      });
      expect(msg.parts![1]).toEqual({ type: 'text', text: '2 + 2 = 4' });
    });

    it('preserves parts through error result', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => result.current.sendMessage('test'));
      act(() => handlers.onMessage?.(assistantEvent()));
      act(() => handlers.onMessage?.(contentBlockStart(0, 'thinking')));
      act(() => handlers.onMessage?.(thinkingDelta('Starting to think...')));
      act(() => handlers.onMessage?.(resultEvent({ isError: true })));

      const msg = result.current.messages[1];
      expect(msg.status).toBe('error');
      expect(msg.parts).toHaveLength(1);
      expect(msg.parts![0]).toEqual({ type: 'reasoning', text: 'Starting to think...' });
    });

    it('handles thinking delta without prior content_block_start', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => result.current.sendMessage('test'));
      act(() => handlers.onMessage?.(assistantEvent()));
      // Thinking delta without content_block_start — no reasoning part to append to
      act(() => handlers.onMessage?.(thinkingDelta('orphan thinking')));

      const msg = result.current.messages[1];
      // Parts array is empty since there was no content_block_start to create a reasoning part
      expect(msg.parts).toEqual([]);
    });
  });

  // ── Permission requests (control_request) ───────────────

  describe('permission requests', () => {
    function controlRequestEvent(
      requestId: string,
      tool: string,
      input: Record<string, unknown> = {}
    ) {
      return JSON.stringify({
        type: 'control_request',
        controlType: 'can_use_tool',
        request_id: requestId,
        tool,
        input,
      });
    }

    it('starts with empty pendingPermissions', () => {
      setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      expect(result.current.pendingPermissions).toEqual([]);
    });

    it('queues a control_request as a pending permission', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          controlRequestEvent('req-abc', 'Bash', { command: 'rm -rf /tmp/test' })
        )
      );

      expect(result.current.pendingPermissions).toHaveLength(1);
      expect(result.current.pendingPermissions[0].request_id).toBe('req-abc');
      expect(result.current.pendingPermissions[0].controlType).toBe('can_use_tool');
      expect(result.current.pendingPermissions[0].tool).toBe('Bash');
      expect(result.current.pendingPermissions[0].input).toEqual({ command: 'rm -rf /tmp/test' });
    });

    it('queues multiple permission requests', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => handlers.onMessage?.(controlRequestEvent('req-1', 'Bash', { command: 'ls' })));
      act(() =>
        handlers.onMessage?.(controlRequestEvent('req-2', 'Write', { file_path: '/tmp/x' }))
      );

      expect(result.current.pendingPermissions).toHaveLength(2);
      expect(result.current.pendingPermissions[0].request_id).toBe('req-1');
      expect(result.current.pendingPermissions[1].request_id).toBe('req-2');
    });

    it('ignores control_request without request_id', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(JSON.stringify({ type: 'control_request', tool: 'Bash', input: {} }))
      );

      expect(result.current.pendingPermissions).toHaveLength(0);
    });

    it('uses defaults for missing controlType/tool/input', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(JSON.stringify({ type: 'control_request', request_id: 'req-min' }))
      );

      expect(result.current.pendingPermissions).toHaveLength(1);
      expect(result.current.pendingPermissions[0].controlType).toBe('can_use_tool');
      expect(result.current.pendingPermissions[0].tool).toBe('unknown');
      expect(result.current.pendingPermissions[0].input).toEqual({});
    });

    it('does not add permission to chat messages', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => handlers.onMessage?.(controlRequestEvent('req-1', 'Bash', { command: 'ls' })));

      expect(result.current.messages).toHaveLength(0);
    });
  });

  // ── respondToPermission ─────────────────────────────────

  describe('respondToPermission', () => {
    function controlRequestEvent(requestId: string, tool: string) {
      return JSON.stringify({
        type: 'control_request',
        controlType: 'can_use_tool',
        request_id: requestId,
        tool,
        input: { command: 'test' },
      });
    }

    it('sends permission_response with allow and removes from queue', () => {
      const { handlers, sendJson } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => handlers.onMessage?.(controlRequestEvent('req-1', 'Bash')));

      expect(result.current.pendingPermissions).toHaveLength(1);

      act(() => result.current.respondToPermission('req-1', 'allow'));

      expect(sendJson).toHaveBeenCalledWith({
        type: 'permission_response',
        request_id: 'req-1',
        behavior: 'allow',
        updated_input: {},
        updated_permissions: [],
      });
      expect(result.current.pendingPermissions).toHaveLength(0);
    });

    it('sends permission_response with deny', () => {
      const { handlers, sendJson } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => handlers.onMessage?.(controlRequestEvent('req-2', 'Write')));

      act(() => result.current.respondToPermission('req-2', 'deny'));

      expect(sendJson).toHaveBeenCalledWith({
        type: 'permission_response',
        request_id: 'req-2',
        behavior: 'deny',
        updated_input: {},
        updated_permissions: [],
      });
      expect(result.current.pendingPermissions).toHaveLength(0);
    });

    it('sends permission_response with allowForever', () => {
      const { handlers, sendJson } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => handlers.onMessage?.(controlRequestEvent('req-3', 'Read')));

      act(() => result.current.respondToPermission('req-3', 'allowForever'));

      expect(sendJson).toHaveBeenCalledWith({
        type: 'permission_response',
        request_id: 'req-3',
        behavior: 'allowForever',
        updated_input: {},
        updated_permissions: [],
      });
    });

    it('passes updatedInput when provided', () => {
      const { handlers, sendJson } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => handlers.onMessage?.(controlRequestEvent('req-4', 'Bash')));

      act(() => result.current.respondToPermission('req-4', 'allow', { command: 'echo safe' }));

      expect(sendJson).toHaveBeenCalledWith({
        type: 'permission_response',
        request_id: 'req-4',
        behavior: 'allow',
        updated_input: { command: 'echo safe' },
        updated_permissions: [],
      });
    });

    it('only removes the matching request from the queue', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => handlers.onMessage?.(controlRequestEvent('req-a', 'Bash')));
      act(() => handlers.onMessage?.(controlRequestEvent('req-b', 'Write')));

      expect(result.current.pendingPermissions).toHaveLength(2);

      act(() => result.current.respondToPermission('req-a', 'allow'));

      expect(result.current.pendingPermissions).toHaveLength(1);
      expect(result.current.pendingPermissions[0].request_id).toBe('req-b');
    });
  });

  // ── Control actions ─────────────────────────────────────

  describe('control actions', () => {
    it('sendInterrupt sends interrupt message', () => {
      const { handlers, sendJson } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => result.current.sendInterrupt());

      expect(sendJson).toHaveBeenCalledWith({ type: 'interrupt' });
    });

    it('sendSetModel sends set_model message', () => {
      const { handlers, sendJson } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => result.current.sendSetModel('claude-opus-4-6'));

      expect(sendJson).toHaveBeenCalledWith({
        type: 'set_model',
        model: 'claude-opus-4-6',
      });
    });

    it('sendSetMaxThinkingTokens sends set_max_thinking_tokens message', () => {
      const { handlers, sendJson } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => result.current.sendSetMaxThinkingTokens(8192));

      expect(sendJson).toHaveBeenCalledWith({
        type: 'set_max_thinking_tokens',
        max_thinking_tokens: 8192,
      });
    });

    it('sendRewindFiles sends rewind_files message', () => {
      const { handlers, sendJson } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => result.current.sendRewindFiles());

      expect(sendJson).toHaveBeenCalledWith({ type: 'rewind_files' });
    });
  });

  // ── Attachment support ──────────────────────────────────

  describe('sendMessage with attachments', () => {
    it('sends content blocks array when attachments are provided', () => {
      const { handlers, sendJson } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        result.current.sendMessage('describe this image', [
          {
            type: 'image',
            source: { type: 'base64', media_type: 'image/png', data: 'iVBOR...' },
          },
        ])
      );

      expect(sendJson).toHaveBeenCalledWith({
        type: 'user',
        content: [
          { type: 'text', text: 'describe this image' },
          { type: 'image', source: { type: 'base64', media_type: 'image/png', data: 'iVBOR...' } },
        ],
      });
      expect(result.current.messages).toHaveLength(1);
      expect(result.current.messages[0].content).toBe('describe this image');
      expect(result.current.isRunning).toBe(true);
    });

    it('sends plain string when no attachments', () => {
      const { handlers, sendJson } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => result.current.sendMessage('hello'));

      expect(sendJson).toHaveBeenCalledWith({ type: 'user', content: 'hello' });
    });

    it('sends attachments-only message without text', () => {
      const { handlers, sendJson } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        result.current.sendMessage('', [
          {
            type: 'image',
            source: { type: 'base64', media_type: 'image/jpeg', data: '/9j/4A...' },
          },
        ])
      );

      expect(sendJson).toHaveBeenCalledWith({
        type: 'user',
        content: [
          {
            type: 'image',
            source: { type: 'base64', media_type: 'image/jpeg', data: '/9j/4A...' },
          },
        ],
      });
      expect(result.current.messages).toHaveLength(1);
      expect(result.current.messages[0].content).toBe('');
    });

    it('stores attachment metadata on user message', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      const meta = [
        { name: 'photo.png', type: 'image' as const, size: 1024, contentType: 'image/png' },
      ];

      act(() => handlers.onOpen?.());
      act(() =>
        result.current.sendMessage(
          'check this',
          [{ type: 'image', source: { type: 'base64', media_type: 'image/png', data: 'abc' } }],
          meta
        )
      );

      expect(result.current.messages[0].attachments).toEqual(meta);
    });

    it('sends document content block for PDFs', () => {
      const { handlers, sendJson } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        result.current.sendMessage('read this pdf', [
          {
            type: 'document',
            source: { type: 'base64', media_type: 'application/pdf', data: 'JVBERi...' },
          },
        ])
      );

      expect(sendJson).toHaveBeenCalledWith({
        type: 'user',
        content: [
          { type: 'text', text: 'read this pdf' },
          {
            type: 'document',
            source: { type: 'base64', media_type: 'application/pdf', data: 'JVBERi...' },
          },
        ],
      });
    });

    it('rejects send with no text and no attachments', () => {
      const { handlers, sendJson } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() => result.current.sendMessage('', []));

      expect(sendJson).not.toHaveBeenCalled();
      expect(result.current.messages).toHaveLength(0);
    });
  });

  // ── tool_use streaming ──────────────────────────────────

  describe('tool_use streaming', () => {
    it('tracks tool_use parts from content_block_start', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'assistant',
            message: { id: 'a1', role: 'assistant', model: 'test', content: [] },
          })
        )
      );
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'content_block_start',
            index: 0,
            content_block: { type: 'tool_use', id: 'tool-1', name: 'Bash' },
          })
        )
      );

      const msg = result.current.messages[0];
      expect(msg.parts).toBeDefined();
      const toolPart = msg.parts?.find(p => p.type === 'tool_use');
      expect(toolPart).toBeDefined();
      expect(toolPart?.type).toBe('tool_use');
      if (toolPart?.type === 'tool_use') {
        expect(toolPart.name).toBe('Bash');
        expect(toolPart.id).toBe('tool-1');
      }
    });

    it('accumulates input_json_delta during streaming', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'assistant',
            message: { id: 'a1', role: 'assistant', model: 'test', content: [] },
          })
        )
      );
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'content_block_start',
            index: 0,
            content_block: { type: 'tool_use', id: 'tool-2', name: 'Read' },
          })
        )
      );
      // Send input_json_delta chunks
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'content_block_delta',
            delta: { type: 'input_json_delta', partial_json: '{"file' },
          })
        )
      );
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'content_block_delta',
            delta: { type: 'input_json_delta', partial_json: '_path":"/src/app.ts"}' },
          })
        )
      );
      // Finalize with content_block_stop
      act(() => handlers.onMessage?.(JSON.stringify({ type: 'content_block_stop' })));

      const msg = result.current.messages[0];
      const toolPart = msg.parts?.find(p => p.type === 'tool_use');
      expect(toolPart).toBeDefined();
      if (toolPart?.type === 'tool_use') {
        expect(toolPart.input).toEqual({ file_path: '/src/app.ts' });
      }
    });

    it('handles content_block_stop with invalid JSON gracefully', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'assistant',
            message: { id: 'a1', role: 'assistant', model: 'test', content: [] },
          })
        )
      );
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'content_block_start',
            index: 0,
            content_block: { type: 'tool_use', id: 'tool-3', name: 'Bash' },
          })
        )
      );
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'content_block_delta',
            delta: { type: 'input_json_delta', partial_json: '{invalid' },
          })
        )
      );
      act(() => handlers.onMessage?.(JSON.stringify({ type: 'content_block_stop' })));

      // Should not crash, tool part should have empty input
      const msg = result.current.messages[0];
      const toolPart = msg.parts?.find(p => p.type === 'tool_use');
      expect(toolPart).toBeDefined();
      if (toolPart?.type === 'tool_use') {
        expect(toolPart.input).toEqual({});
      }
    });

    it('handles content_block_stop without tool data (no-op)', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'assistant',
            message: { id: 'a1', role: 'assistant', model: 'test', content: [] },
          })
        )
      );
      // content_block_stop without any tool_use start — should be a no-op
      act(() => handlers.onMessage?.(JSON.stringify({ type: 'content_block_stop' })));

      expect(result.current.messages).toHaveLength(1);
    });

    it('includes tool parts in finalized result message', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'assistant',
            message: { id: 'a1', role: 'assistant', model: 'test-model', content: [] },
          })
        )
      );
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'content_block_start',
            index: 0,
            content_block: { type: 'tool_use', id: 'tool-4', name: 'Grep' },
          })
        )
      );
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'content_block_delta',
            delta: { type: 'input_json_delta', partial_json: '{"pattern":"TODO"}' },
          })
        )
      );
      act(() => handlers.onMessage?.(JSON.stringify({ type: 'content_block_stop' })));
      // Finalize with result
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'result',
            subtype: 'success',
            result: '',
            total_cost_usd: 0.01,
            num_turns: 1,
          })
        )
      );

      const msg = result.current.messages[0];
      expect(msg.status).toBe('complete');
      expect(msg.parts).toBeDefined();
      const toolPart = msg.parts?.find(p => p.type === 'tool_use');
      expect(toolPart).toBeDefined();
      if (toolPart?.type === 'tool_use') {
        expect(toolPart.name).toBe('Grep');
        expect(toolPart.input).toEqual({ pattern: 'TODO' });
      }
    });
  });

  // ── clearMessages clears permissions too ────────────────

  it('clearMessages also clears pending permissions', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'control_request',
          controlType: 'can_use_tool',
          request_id: 'req-x',
          tool: 'Bash',
          input: {},
        })
      )
    );

    expect(result.current.pendingPermissions).toHaveLength(1);

    act(() => result.current.clearMessages());

    expect(result.current.pendingPermissions).toHaveLength(0);
    expect(result.current.messages).toHaveLength(0);
  });

  // ── Conversation history fetch ──────────────────────────

  describe('conversation history fetch', () => {
    it('fetches history from server on mount', async () => {
      const turns = [
        {
          id: 'turn-1',
          role: 'user',
          content: 'Hello',
          parts: [],
          created_at: '2026-01-01T00:00:00Z',
          metadata: {},
        },
        {
          id: 'turn-2',
          role: 'assistant',
          content: 'Hi there!',
          parts: [],
          created_at: '2026-01-01T00:00:01Z',
          metadata: {},
        },
      ];

      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ turns }),
      });
      vi.stubGlobal('fetch', fetchMock);

      setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test-host/session'));

      // Wait for the async fetch to resolve
      await vi.waitFor(() => {
        expect(result.current.historyLoaded).toBe(true);
      });

      expect(fetchMock).toHaveBeenCalledWith(
        'https://test-host/api/conversation/history',
        expect.objectContaining({ headers: expect.any(Object) })
      );
      expect(result.current.messages).toHaveLength(2);
      expect(result.current.messages[0].role).toBe('user');
      expect(result.current.messages[0].content).toBe('Hello');
      expect(result.current.messages[1].role).toBe('assistant');
      expect(result.current.messages[1].content).toBe('Hi there!');

      vi.unstubAllGlobals();
    });

    it('adds Authorization header when access token is available', async () => {
      vi.mocked(getAccessToken).mockReturnValue('test-access-token');

      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ turns: [] }),
      });
      vi.stubGlobal('fetch', fetchMock);

      setupMock();
      renderHook(() => useSkuldChat('wss://test-host/session'));

      await vi.waitFor(() => {
        expect(fetchMock).toHaveBeenCalled();
      });

      expect(fetchMock).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: 'Bearer test-access-token',
          }),
        })
      );

      vi.mocked(getAccessToken).mockReturnValue(null);
      vi.unstubAllGlobals();
    });

    it('adds activity-indicator message when session is_active with last_activity', async () => {
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            turns: [],
            is_active: true,
            last_activity: 'Analyzing codebase...',
          }),
      });
      vi.stubGlobal('fetch', fetchMock);

      setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test-host/session'));

      await vi.waitFor(() => {
        expect(result.current.historyLoaded).toBe(true);
      });

      // The activity-indicator message should be added as a running assistant message
      const indicator = result.current.messages.find(m => m.id === 'activity-indicator');
      expect(indicator).toBeDefined();
      expect(indicator?.status).toBe('running');
      expect(indicator?.content).toBe('Analyzing codebase...');

      vi.unstubAllGlobals();
    });

    it('falls back to sessionStorage on fetch failure', async () => {
      const fetchMock = vi.fn().mockRejectedValue(new Error('Network error'));
      vi.stubGlobal('fetch', fetchMock);

      const { storeMock } = setupMock();
      storeMock.getMessages.mockReturnValue([
        {
          id: 'cached-1',
          role: 'user',
          content: 'Cached message',
          createdAt: new Date(),
          status: 'complete',
        },
      ]);

      const { result } = renderHook(() => useSkuldChat('wss://test-host/session'));

      await vi.waitFor(() => {
        expect(result.current.historyLoaded).toBe(true);
      });

      // Should have used cached messages
      expect(result.current.messages).toHaveLength(1);
      expect(result.current.messages[0].content).toBe('Cached message');

      vi.unstubAllGlobals();
    });

    it('resets historyLoaded on session URL change', async () => {
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ turns: [] }),
      });
      vi.stubGlobal('fetch', fetchMock);

      setupMock();
      const { result, rerender } = renderHook(
        ({ url }: { url: string | null }) => useSkuldChat(url),
        { initialProps: { url: 'wss://host-a/session' } }
      );

      await vi.waitFor(() => {
        expect(result.current.historyLoaded).toBe(true);
      });

      // Change URL — should reset historyLoaded and re-fetch
      fetchMock.mockClear();
      fetchMock.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ turns: [] }),
      });

      rerender({ url: 'wss://host-b/session' });

      await vi.waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          'https://host-b/api/conversation/history',
          expect.objectContaining({ headers: expect.any(Object) })
        );
      });

      vi.unstubAllGlobals();
    });

    it('exposes historyLoaded as false initially', () => {
      setupMock();
      // Don't stub fetch — let it be undefined (will fail naturally)
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue({
          ok: true,
          json: () => Promise.resolve({ turns: [] }),
        })
      );

      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      // historyLoaded starts as false before fetch resolves
      expect(result.current.historyLoaded).toBe(false);

      vi.unstubAllGlobals();
    });

    it('falls back to sessionStorage when URL cannot be parsed to HTTP', async () => {
      // Use a URL scheme that wsUrlToHttpBase cannot parse to HTTP
      // by passing a non-ws/wss URL that still has a valid format
      const { storeMock } = setupMock();
      storeMock.getMessages.mockReturnValue([
        {
          id: 'cached-2',
          role: 'user' as const,
          content: 'Cached via fallback',
          createdAt: new Date(),
          status: 'complete' as const,
        },
      ]);

      // Temporarily make URL constructor throw for our test URL
      const originalURL = globalThis.URL;
      const badUrl = 'wss://valid-host/session';

      // We need to test the httpBase === null path. Since wsUrlToHttpBase
      // only returns null when URL constructor throws, we stub it.
      // The easiest way: use a URL that causes wsUrlToHttpBase to return null
      // by making the URL constructor throw for it.
      vi.stubGlobal(
        'URL',
        class BadURL {
          constructor() {
            throw new Error('Cannot parse URL');
          }
        }
      );

      const { result } = renderHook(() => useSkuldChat(badUrl));

      // The setTimeout fallback should fire asynchronously
      await vi.waitFor(() => {
        expect(result.current.historyLoaded).toBe(true);
      });

      // Should have used cached messages from store
      expect(result.current.messages).toHaveLength(1);
      expect(result.current.messages[0].content).toBe('Cached via fallback');

      vi.stubGlobal('URL', originalURL);
    });

    it('handles fetch returning empty turns array', async () => {
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ turns: [] }),
      });
      vi.stubGlobal('fetch', fetchMock);

      setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test-host/session'));

      await vi.waitFor(() => {
        expect(result.current.historyLoaded).toBe(true);
      });

      // Empty turns should not add any messages
      expect(result.current.messages).toEqual([]);

      vi.unstubAllGlobals();
    });

    it('handles fetch returning no turns field', async () => {
      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({}),
      });
      vi.stubGlobal('fetch', fetchMock);

      setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test-host/session'));

      await vi.waitFor(() => {
        expect(result.current.historyLoaded).toBe(true);
      });

      expect(result.current.messages).toEqual([]);

      vi.unstubAllGlobals();
    });
  });

  // ── Additional branch coverage ──────────────────────────

  it('finalizes in-flight message on WebSocket error with streaming content', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));
    act(() => handlers.onMessage?.(assistantEvent()));
    act(() => handlers.onMessage?.(contentBlockStart(0, 'text')));
    act(() => handlers.onMessage?.(contentBlockDelta('partial content')));

    expect(result.current.isRunning).toBe(true);
    expect(result.current.messages[1].status).toBe('running');

    // Trigger WebSocket error — should finalize the in-flight message
    act(() => handlers.onError?.(new Event('error')));

    expect(result.current.isRunning).toBe(false);
    expect(result.current.connected).toBe(false);
    // The in-flight message should be finalized as complete
    expect(result.current.messages[1].status).toBe('complete');
    expect(result.current.messages[1].content).toBe('partial content');
  });

  it('finalizes in-flight message with parts on WebSocket close', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));
    act(() => handlers.onMessage?.(assistantEvent()));
    act(() => handlers.onMessage?.(contentBlockStart(0, 'thinking')));
    act(() => handlers.onMessage?.(thinkingDelta('Some reasoning')));
    act(() => handlers.onMessage?.(contentBlockStart(1, 'text')));
    act(() => handlers.onMessage?.(contentBlockDelta('response text')));

    // WebSocket closes while streaming
    act(() => handlers.onClose?.(1006, 'abnormal'));

    expect(result.current.messages[1].status).toBe('complete');
    expect(result.current.messages[1].content).toBe('response text');
    expect(result.current.messages[1].parts).toHaveLength(2);
    expect(result.current.messages[1].parts![0].type).toBe('reasoning');
    expect(result.current.messages[1].parts![1].type).toBe('text');
  });

  it('finalizes previous assistant message when a new assistant event arrives', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());

    // First assistant turn
    act(() => handlers.onMessage?.(assistantEvent('claude-sonnet-4-5-20250514', 100)));
    act(() => handlers.onMessage?.(contentBlockDelta('First response')));

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].status).toBe('running');

    // Second assistant event arrives without a result event for the first
    act(() => handlers.onMessage?.(assistantEvent('claude-sonnet-4-5-20250514', 200)));

    // First message should be finalized
    expect(result.current.messages[0].status).toBe('complete');
    expect(result.current.messages[0].content).toBe('First response');

    // Second message should be running
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[1].status).toBe('running');
  });

  it('handles result event with is_error and result text', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => result.current.sendMessage('test'));
    act(() => handlers.onMessage?.(assistantEvent()));
    // Error result with result text (no streaming content)
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'result',
          subtype: 'error',
          is_error: true,
          result: 'Rate limit exceeded',
          total_cost_usd: 0.0,
          num_turns: 0,
          duration_ms: 100,
          session_id: 'sess-err',
        })
      )
    );

    expect(result.current.messages[1].status).toBe('error');
    expect(result.current.messages[1].content).toBe('Rate limit exceeded');
  });

  it('handles hook_response with no output', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'system',
          subtype: 'hook_response',
          hook_name: 'TestHook',
        })
      )
    );

    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0].content).toBe('Hook TestHook: ');
  });

  // ── Available Commands ───────────────────────────────────────

  it('updates availableCommands when available_commands event is received', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'available_commands',
          slash_commands: ['/help', '/clear'],
          skills: ['commit'],
        })
      )
    );

    expect(result.current.availableCommands).toHaveLength(3);
  });

  // ── User Confirmed ─────────────────────────────────────────

  it('silently consumes user_confirmed event without changing messages', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() => handlers.onMessage?.(JSON.stringify({ type: 'user_confirmed' })));

    expect(result.current.messages).toEqual([]);
  });

  // ── Capabilities ────────────────────────────────────────────

  it('starts with DEFAULT_CAPABILITIES (all false except send_message)', () => {
    setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    expect(result.current.capabilities).toEqual(DEFAULT_CAPABILITIES);
    expect(result.current.capabilities.send_message).toBe(true);
    expect(result.current.capabilities.interrupt).toBe(false);
    expect(result.current.capabilities.set_model).toBe(false);
  });

  it('includes capabilities in hook return value', () => {
    setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    expect(result.current).toHaveProperty('capabilities');
    expect(result.current.capabilities).toBeDefined();
    expect(typeof result.current.capabilities.send_message).toBe('boolean');
    expect(typeof result.current.capabilities.interrupt).toBe('boolean');
  });

  it('updates capabilities when capabilities event is received', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'capabilities',
          cli_websocket: true,
          interrupt: true,
          set_model: true,
          set_thinking_tokens: true,
          rewind_files: true,
          permission_requests: true,
          slash_commands: true,
          skills: true,
        })
      )
    );

    expect(result.current.capabilities.cli_websocket).toBe(true);
    expect(result.current.capabilities.interrupt).toBe(true);
    expect(result.current.capabilities.set_model).toBe(true);
    expect(result.current.capabilities.set_thinking_tokens).toBe(true);
    expect(result.current.capabilities.rewind_files).toBe(true);
    expect(result.current.capabilities.session_resume).toBe(false);
    expect(result.current.capabilities.set_permission_mode).toBe(false);
    expect(result.current.capabilities.mcp_set_servers).toBe(false);
  });

  it('treats missing capabilities fields as false', () => {
    const { handlers } = setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test/session'));

    act(() => handlers.onOpen?.());
    act(() =>
      handlers.onMessage?.(
        JSON.stringify({
          type: 'capabilities',
          interrupt: true,
        })
      )
    );

    expect(result.current.capabilities.interrupt).toBe(true);
    expect(result.current.capabilities.set_model).toBe(false);
    expect(result.current.capabilities.cli_websocket).toBe(false);
  });

  // ── transformTurns: participant field mapping ────────────────────────────

  it('maps participant_meta from history to ParticipantMeta', async () => {
    const turns = [
      {
        id: 'turn-p1',
        role: 'assistant',
        content: 'Hi from bot',
        parts: [],
        created_at: '2026-01-01T00:00:00Z',
        metadata: {},
        participant_id: 'agent-1',
        participant_meta: {
          peer_id: 'agent-1',
          persona: 'Ravn',
          color: 'p2',
          participant_type: 'ravn',
          gateway_url: 'wss://gateway/ravn',
        },
        thread_id: 'thread-abc',
        visibility: 'public',
      },
    ];

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ turns }),
    });
    vi.stubGlobal('fetch', fetchMock);

    setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test-host/session'));

    await vi.waitFor(() => {
      expect(result.current.historyLoaded).toBe(true);
    });

    const msg = result.current.messages[0];
    expect(msg.participantId).toBe('agent-1');
    expect(msg.participant?.peerId).toBe('agent-1');
    expect(msg.participant?.persona).toBe('Ravn');
    expect(msg.participant?.color).toBe('p2');
    expect(msg.participant?.participantType).toBe('ravn');
    expect(msg.participant?.gatewayUrl).toBe('wss://gateway/ravn');
    expect(msg.threadId).toBe('thread-abc');
    expect(msg.visibility).toBe('public');

    vi.unstubAllGlobals();
  });

  it('maps turns without participant_meta to undefined participant fields', async () => {
    const turns = [
      {
        id: 'turn-np',
        role: 'user',
        content: 'plain',
        parts: [],
        created_at: '2026-01-01T00:00:00Z',
        metadata: {},
      },
    ];

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ turns }),
    });
    vi.stubGlobal('fetch', fetchMock);

    setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test-host/session'));

    await vi.waitFor(() => {
      expect(result.current.historyLoaded).toBe(true);
    });

    const msg = result.current.messages[0];
    expect(msg.participantId).toBeUndefined();
    expect(msg.participant).toBeUndefined();
    expect(msg.threadId).toBeUndefined();
    expect(msg.visibility).toBeUndefined();

    vi.unstubAllGlobals();
  });

  it('maps participant_meta with null gateway_url to undefined gatewayUrl', async () => {
    const turns = [
      {
        id: 'turn-human',
        role: 'user',
        content: 'hello',
        parts: [],
        created_at: '2026-01-01T00:00:00Z',
        metadata: {},
        participant_id: 'human-1',
        participant_meta: {
          peer_id: 'human-1',
          persona: 'Alice',
          color: 'p1',
          participant_type: 'human',
          gateway_url: null,
        },
      },
    ];

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ turns }),
    });
    vi.stubGlobal('fetch', fetchMock);

    setupMock();
    const { result } = renderHook(() => useSkuldChat('wss://test-host/session'));

    await vi.waitFor(() => {
      expect(result.current.historyLoaded).toBe(true);
    });

    const msg = result.current.messages[0];
    expect(msg.participant?.participantType).toBe('human');
    expect(msg.participant?.gatewayUrl).toBeUndefined();

    vi.unstubAllGlobals();
  });

  // ── Room event handlers ──────────────────────────────────────

  describe('room event handlers', () => {
    it('adds participant on participant_joined', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'participant_joined',
            peer_id: 'peer-1',
            persona: 'Ravn-A',
            color: 'p1',
            participant_type: 'ravn',
          })
        )
      );

      expect(result.current.participants.size).toBe(1);
      const p = result.current.participants.get('peer-1');
      expect(p?.persona).toBe('Ravn-A');
      expect(p?.color).toBe('p1');
      expect(p?.status).toBe('idle');
    });

    it('ignores participant_joined with empty peer_id', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({ type: 'participant_joined', peer_id: '', persona: 'X', color: 'p2' })
        )
      );

      expect(result.current.participants.size).toBe(0);
    });

    it('removes participant on participant_left', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'participant_joined',
            peer_id: 'peer-1',
            persona: 'A',
            color: 'p1',
          })
        )
      );
      expect(result.current.participants.size).toBe(1);

      act(() =>
        handlers.onMessage?.(JSON.stringify({ type: 'participant_left', peer_id: 'peer-1' }))
      );
      expect(result.current.participants.size).toBe(0);
    });

    it('ignores participant_left with empty peer_id', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'participant_joined',
            peer_id: 'peer-1',
            persona: 'A',
            color: 'p1',
          })
        )
      );
      act(() => handlers.onMessage?.(JSON.stringify({ type: 'participant_left', peer_id: '' })));
      // Still 1 participant — empty peer_id was ignored
      expect(result.current.participants.size).toBe(1);
    });

    it('initializes participants map on room_state', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'room_state',
            participants: [
              { peer_id: 'p1', persona: 'Ravn-A', color: 'p1', participant_type: 'ravn' },
              { peer_id: 'p2', persona: 'Ravn-B', color: 'p2', participant_type: 'ravn' },
            ],
          })
        )
      );

      expect(result.current.participants.size).toBe(2);
      expect(result.current.participants.get('p1')?.persona).toBe('Ravn-A');
      expect(result.current.participants.get('p2')?.persona).toBe('Ravn-B');
    });

    it('skips room_state participants with empty peer_id', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'room_state',
            participants: [
              { peer_id: '', persona: 'Ghost', color: 'p5' },
              { peer_id: 'p1', persona: 'Ravn-A', color: 'p1' },
            ],
          })
        )
      );

      expect(result.current.participants.size).toBe(1);
    });

    it('appends a room_message to messages', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'room_message',
            id: 'rm-1',
            role: 'assistant',
            content: 'Room message content',
            participant_id: 'peer-1',
            participant: {
              peer_id: 'peer-1',
              persona: 'Ravn-A',
              color: 'p1',
              participant_type: 'ravn',
            },
            thread_id: 'thread-xyz',
            visibility: 'internal',
          })
        )
      );

      expect(result.current.messages).toHaveLength(1);
      const msg = result.current.messages[0];
      expect(msg.id).toBe('rm-1');
      expect(msg.content).toBe('Room message content');
      expect(msg.participant?.persona).toBe('Ravn-A');
      expect(msg.threadId).toBe('thread-xyz');
      expect(msg.visibility).toBe('internal');
    });

    it('room_message without participant sets participant to undefined', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({ type: 'room_message', id: 'rm-2', content: 'no participant' })
        )
      );

      const msg = result.current.messages[0];
      expect(msg.participant).toBeUndefined();
    });

    it('updates participant status on room_activity', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'participant_joined',
            peer_id: 'peer-1',
            persona: 'Ravn-A',
            color: 'p1',
          })
        )
      );
      expect(result.current.participants.get('peer-1')?.status).toBe('idle');

      act(() =>
        handlers.onMessage?.(
          JSON.stringify({ type: 'room_activity', peer_id: 'peer-1', status: 'thinking' })
        )
      );

      expect(result.current.participants.get('peer-1')?.status).toBe('thinking');
    });

    it('ignores room_activity for unknown peer_id', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({ type: 'room_activity', peer_id: 'unknown', status: 'thinking' })
        )
      );

      // No crash, no participants added
      expect(result.current.participants.size).toBe(0);
    });

    it('room_message with camelCase participantId resolves sender', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({
            type: 'room_message',
            id: 'rm-camel',
            content: 'camelCase test',
            participantId: 'peer-2',
            participant: {
              peer_id: 'peer-2',
              persona: 'Skuld',
              color: 'cyan',
              participant_type: 'ravn',
            },
          })
        )
      );

      expect(result.current.messages).toHaveLength(1);
      const msg = result.current.messages[0];
      expect(msg.participantId).toBe('peer-2');
      expect(msg.participant?.color).toBe('cyan');
    });

    it('ignores room_activity with empty peer_id', () => {
      const { handlers } = setupMock();
      const { result } = renderHook(() => useSkuldChat('wss://test/session'));

      act(() => handlers.onOpen?.());
      act(() =>
        handlers.onMessage?.(
          JSON.stringify({ type: 'room_activity', peer_id: '', status: 'thinking' })
        )
      );

      expect(result.current.participants.size).toBe(0);
    });
  });
});
