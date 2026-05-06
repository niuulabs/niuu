import { describe, expect, it, vi } from 'vitest';
import {
  formatOutcomeContent,
  getNumber,
  getStorageKey,
  getString,
  getStringArray,
  makeSingleParticipant,
  parseEvent,
  parseParticipantMeta,
  pushOutcomeField,
  reviveAgentEvents,
  reviveMeshEvents,
  reviveMessages,
  safeSessionStorageGet,
  safeSessionStorageSet,
  serializeAgentEvents,
  serializeMeshEvents,
  serializeMessages,
  stringifyOutcomeValue,
  transformTurns,
} from './useSkuldChat';

describe('useSkuldChat helpers', () => {
  it('builds the storage key and tolerates sessionStorage failures', () => {
    expect(getStorageKey('ws://example')).toContain('ws://example');

    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(safeSessionStorageGet('ws://example')).toBeNull();
    getItem.mockRestore();

    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('full');
    });
    expect(() => safeSessionStorageSet('ws://example', { messages: [] })).not.toThrow();
    setItem.mockRestore();
  });

  it('persists and revives messages, mesh events, and agent events', () => {
    const createdAt = new Date('2026-05-01T10:18:36.889Z');
    const meshTimestamp = new Date('2026-05-01T10:19:00.000Z');
    const messages = [
      { id: 'done', role: 'assistant', content: 'done', createdAt, status: 'done' as const },
      { id: 'running', role: 'assistant', content: 'skip', createdAt, status: 'running' as const },
    ];
    const serializedMessages = serializeMessages(messages as never);
    expect(serializedMessages).toHaveLength(1);
    expect(reviveMessages(serializedMessages)[0]?.createdAt).toEqual(createdAt);

    const meshEvents = [
      {
        id: 'mesh-1',
        timestamp: meshTimestamp,
        participantId: 'peer-1',
        participant: { peerId: 'peer-1', persona: 'reviewer' },
        eventType: 'review.completed',
        summary: 'Looks good',
        content: '```outcome\nsummary: Looks good\n```',
      },
    ];
    expect(reviveMeshEvents(serializeMeshEvents(meshEvents as never))[0]?.timestamp).toEqual(
      meshTimestamp,
    );

    const agentEvents = new Map([
      [
        'peer-1',
        [{ id: 'evt-1', frameType: 'thought', data: 'inspect', timestamp: meshTimestamp }],
      ],
    ]);
    const revived = reviveAgentEvents(serializeAgentEvents(agentEvents as never));
    expect(revived.get('peer-1')?.[0]?.timestamp).toEqual(meshTimestamp);
  });

  it('extracts participant metadata with multiple key shapes', () => {
    expect(makeSingleParticipant()).toMatchObject({
      participantType: 'skuld',
      peerId: 'skuld-primary',
    });

    const raw = {
      peerId: 'peer-1',
      persona: 'reviewer',
      display_name: 'Reviewer',
      participantType: 'ravn',
      tools: ['bash', 1, 'rg'],
      gatewayLatencyMs: 12,
      status: 'thinking',
    };
    expect(getString(raw, 'display_name', 'displayName')).toBe('Reviewer');
    expect(getNumber(raw, 'gateway_latency_ms', 'gatewayLatencyMs')).toBe(12);
    expect(getStringArray(raw, 'tools')).toEqual(['bash', 'rg']);
    expect(parseParticipantMeta(raw)).toMatchObject({
      peerId: 'peer-1',
      displayName: 'Reviewer',
      status: 'thinking',
      tools: ['bash', 'rg'],
    });
    expect(parseParticipantMeta({})).toBeUndefined();
  });

  it('transforms conversation turns into chat messages', () => {
    const turns = [
      {
        id: 'turn-1',
        role: 'assistant',
        content: 'Done',
        created_at: '2026-05-01T10:18:36.889091+00:00',
        participant_meta: { peer_id: 'peer-1', persona: 'reviewer' },
        metadata: { source_platform: 'telegram' },
        visibility: 'public',
        thread_id: 'thread-1',
      },
    ];

    expect(transformTurns(turns as never)).toEqual([
      expect.objectContaining({
        id: 'turn-1',
        role: 'assistant',
        content: 'Done',
        threadId: 'thread-1',
        visibility: 'public',
        participant: expect.objectContaining({ peerId: 'peer-1' }),
      }),
    ]);
  });

  it('formats outcome payloads for strings, JSON values, and multiline content', () => {
    expect(stringifyOutcomeValue({ files: ['a.ts'] })).toBe('{"files":["a.ts"]}');
    expect(stringifyOutcomeValue(undefined)).toBe('');

    const lines: string[] = [];
    pushOutcomeField(lines, 'summary', 'Looks good');
    pushOutcomeField(lines, 'details', 'line one\nline two');
    expect(lines).toEqual(['summary: Looks good', 'details: |', '  line one', '  line two']);

    expect(
      formatOutcomeContent({
        type: 'room_outcome',
        eventType: 'review.completed',
        verdict: 'approved',
        summary: 'Ship it',
        fields: { success: true, files: ['README.md'] },
      } as never),
    ).toContain('files: ["README.md"]');

    expect(
      formatOutcomeContent({ type: 'room_outcome', eventType: 'fallback.event' } as never),
    ).toContain('event_type: fallback.event');
  });

  it('parses websocket events with and without data prefixes', () => {
    expect(parseEvent('data: {"type":"ping"}')).toEqual({ type: 'ping' });
    expect(parseEvent('{"type":"pong"}')).toEqual({ type: 'pong' });
    expect(parseEvent('not-json')).toBeNull();
  });
});
