/**
 * Tests for participant field serialization in chat.store.
 * Verifies new optional fields survive the sessionStorage round-trip.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useChatStore } from './chat.store';
import type { SkuldChatMessage, ParticipantMeta } from '@/modules/shared/hooks/useSkuldChat';

function makeMessage(overrides: Partial<SkuldChatMessage> = {}): SkuldChatMessage {
  return {
    id: `msg-${Math.random().toString(36).slice(2, 8)}`,
    role: 'assistant',
    content: 'Hello',
    createdAt: new Date('2025-06-01T12:00:00Z'),
    status: 'complete',
    ...overrides,
  };
}

const humanParticipant: ParticipantMeta = {
  peerId: 'user-123',
  persona: 'Alice',
  color: 'p1',
  participantType: 'human',
};

const ravnParticipant: ParticipantMeta = {
  peerId: 'agent-456',
  persona: 'Ravn',
  color: 'p2',
  participantType: 'ravn',
  gatewayUrl: 'wss://gateway.example.com/ravn',
};

describe('chat store — participant field serialization', () => {
  beforeEach(() => {
    useChatStore.setState({ sessions: {} });
  });

  it('preserves participantId through serialization round-trip', () => {
    const { setMessages, getMessages } = useChatStore.getState();
    const url = 'wss://host/session';
    const msgs = [makeMessage({ participantId: 'user-123' })];

    setMessages(url, msgs);
    const restored = getMessages(url);

    expect(restored[0].participantId).toBe('user-123');
  });

  it('preserves human participant through serialization round-trip', () => {
    const { setMessages, getMessages } = useChatStore.getState();
    const url = 'wss://host/session';
    const msgs = [makeMessage({ participant: humanParticipant })];

    setMessages(url, msgs);
    const restored = getMessages(url);

    expect(restored[0].participant).toEqual(humanParticipant);
    expect(restored[0].participant?.participantType).toBe('human');
    expect(restored[0].participant?.gatewayUrl).toBeUndefined();
  });

  it('preserves ravn participant with gatewayUrl through serialization round-trip', () => {
    const { setMessages, getMessages } = useChatStore.getState();
    const url = 'wss://host/session';
    const msgs = [makeMessage({ participant: ravnParticipant })];

    setMessages(url, msgs);
    const restored = getMessages(url);

    expect(restored[0].participant?.peerId).toBe('agent-456');
    expect(restored[0].participant?.participantType).toBe('ravn');
    expect(restored[0].participant?.gatewayUrl).toBe('wss://gateway.example.com/ravn');
  });

  it('preserves threadId through serialization round-trip', () => {
    const { setMessages, getMessages } = useChatStore.getState();
    const url = 'wss://host/session';
    const msgs = [makeMessage({ threadId: 'thread-abc' })];

    setMessages(url, msgs);
    const restored = getMessages(url);

    expect(restored[0].threadId).toBe('thread-abc');
  });

  it('preserves visibility through serialization round-trip', () => {
    const { setMessages, getMessages } = useChatStore.getState();
    const url = 'wss://host/session';
    const msgs = [makeMessage({ visibility: 'public' }), makeMessage({ visibility: 'internal' })];

    setMessages(url, msgs);
    const restored = getMessages(url);

    expect(restored[0].visibility).toBe('public');
    expect(restored[1].visibility).toBe('internal');
  });

  it('leaves participant fields undefined when absent (backward compatible)', () => {
    const { setMessages, getMessages } = useChatStore.getState();
    const url = 'wss://host/session';
    const msgs = [makeMessage({ role: 'user', content: 'plain message' })];

    setMessages(url, msgs);
    const restored = getMessages(url);

    expect(restored[0].participantId).toBeUndefined();
    expect(restored[0].participant).toBeUndefined();
    expect(restored[0].threadId).toBeUndefined();
    expect(restored[0].visibility).toBeUndefined();
  });

  it('preserves all participant fields together', () => {
    const { setMessages, getMessages } = useChatStore.getState();
    const url = 'wss://host/session';
    const msgs = [
      makeMessage({
        participantId: 'agent-456',
        participant: ravnParticipant,
        threadId: 'thread-xyz',
        visibility: 'internal',
      }),
    ];

    setMessages(url, msgs);
    const restored = getMessages(url);

    expect(restored[0].participantId).toBe('agent-456');
    expect(restored[0].participant?.persona).toBe('Ravn');
    expect(restored[0].threadId).toBe('thread-xyz');
    expect(restored[0].visibility).toBe('internal');
  });

  it('handles mixed messages with and without participant fields', () => {
    const { setMessages, getMessages } = useChatStore.getState();
    const url = 'wss://host/session';
    const msgs = [
      makeMessage({ id: 'plain', role: 'user', content: 'no participant' }),
      makeMessage({
        id: 'with-p',
        role: 'assistant',
        participantId: 'bot',
        participant: ravnParticipant,
      }),
    ];

    setMessages(url, msgs);
    const restored = getMessages(url);

    expect(restored[0].participantId).toBeUndefined();
    expect(restored[1].participantId).toBe('bot');
    expect(restored[1].participant?.peerId).toBe('agent-456');
  });
});
