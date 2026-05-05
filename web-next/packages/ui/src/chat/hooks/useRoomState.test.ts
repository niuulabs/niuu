import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useRoomState } from './useRoomState';
import type { ChatMessage, RoomParticipant } from '../types';

const now = new Date();

const makeMsg = (overrides: Partial<ChatMessage>): ChatMessage => ({
  id: `msg-${Math.random()}`,
  role: 'assistant',
  content: 'hello',
  createdAt: now,
  status: 'done',
  ...overrides,
});

const p1: RoomParticipant = { peerId: 'p1', persona: 'Ada', color: '#38bdf8' };
const p2: RoomParticipant = { peerId: 'p2', persona: 'Björk', color: '#a78bfa' };

const oneParticipant = new Map([['p1', p1]]);
const twoParticipants = new Map([
  ['p1', p1],
  ['p2', p2],
]);

describe('useRoomState — non-room mode', () => {
  it('isRoomMode is false with <= 1 participant', () => {
    const msgs = [makeMsg({ role: 'user', content: 'hi' })];
    const { result } = renderHook(() => useRoomState(msgs, oneParticipant));
    expect(result.current.isRoomMode).toBe(false);
  });

  it('returns all messages in non-room mode', () => {
    const msgs = [
      makeMsg({ role: 'user', content: 'hi' }),
      makeMsg({ role: 'assistant', content: 'hello' }),
    ];
    const { result } = renderHook(() => useRoomState(msgs, new Map()));
    expect(result.current.visibleMessages).toHaveLength(2);
  });

  it('filters out system messages', () => {
    const msgs = [
      makeMsg({ role: 'user', content: 'hi' }),
      makeMsg({ role: 'system', content: 'Session start', metadata: { messageType: 'system' } }),
    ];
    const { result } = renderHook(() => useRoomState(msgs, new Map()));
    expect(result.current.visibleMessages).toHaveLength(1);
  });

  it('filters out empty done assistant messages', () => {
    const msgs = [
      makeMsg({ role: 'user', content: 'hi' }),
      makeMsg({ role: 'assistant', content: '  ', status: 'done' }),
    ];
    const { result } = renderHook(() => useRoomState(msgs, new Map()));
    expect(result.current.visibleMessages).toHaveLength(1);
  });
});

describe('useRoomState — room mode', () => {
  it('isRoomMode is true with 2+ participants', () => {
    const msgs = [makeMsg({ role: 'user', content: 'hi' })];
    const { result } = renderHook(() => useRoomState(msgs, twoParticipants));
    expect(result.current.isRoomMode).toBe(true);
  });

  it('shows internal messages by default in room mode', () => {
    const msgs = [
      makeMsg({ role: 'assistant', content: 'public', visibility: 'visible' }),
      makeMsg({ role: 'assistant', content: 'secret', visibility: 'internal' }),
    ];
    const { result } = renderHook(() => useRoomState(msgs, twoParticipants));
    expect(result.current.showInternal).toBe(true);
    expect(result.current.visibleMessages.map((m) => m.content)).toEqual(['public', 'secret']);
  });

  it('hides internal messages after toggleInternal', () => {
    const msgs = [
      makeMsg({ role: 'assistant', content: 'public', visibility: 'visible' }),
      makeMsg({ role: 'assistant', content: 'secret', visibility: 'internal' }),
    ];
    const { result } = renderHook(() => useRoomState(msgs, twoParticipants));
    act(() => {
      result.current.toggleInternal();
    });
    expect(result.current.showInternal).toBe(false);
    expect(result.current.visibleMessages.map((m) => m.content)).toEqual(['public']);
  });

  it('filters by participant when activeFilter is set', () => {
    const msgs = [
      makeMsg({ role: 'assistant', content: 'from p1', participant: p1 }),
      makeMsg({ role: 'assistant', content: 'from p2', participant: p2 }),
    ];
    const { result } = renderHook(() => useRoomState(msgs, twoParticipants));
    act(() => {
      result.current.setActiveFilter('p1');
    });
    expect(result.current.visibleMessages.map((m) => m.content)).toEqual(['from p1']);
  });

  it('shows all messages when filter reset to "all"', () => {
    const msgs = [
      makeMsg({ role: 'assistant', content: 'from p1', participant: p1 }),
      makeMsg({ role: 'assistant', content: 'from p2', participant: p2 }),
    ];
    const { result } = renderHook(() => useRoomState(msgs, twoParticipants));
    act(() => {
      result.current.setActiveFilter('p1');
    });
    act(() => {
      result.current.setActiveFilter('all');
    });
    expect(result.current.visibleMessages).toHaveLength(2);
  });
});

describe('useRoomState — thread collapsing', () => {
  it('thread groups form when showInternal=true and multiple internal msgs share threadId', () => {
    const msgs = [
      makeMsg({ role: 'assistant', content: 'a', visibility: 'internal', threadId: 't1' }),
      makeMsg({ role: 'assistant', content: 'b', visibility: 'internal', threadId: 't1' }),
    ];
    const { result } = renderHook(() => useRoomState(msgs, twoParticipants));
    // t1 should be a collapsed thread
    expect(result.current.collapsedThreads.has('t1')).toBe(true);
  });

  it('toggleThread expands a collapsed thread', () => {
    const msgs = [
      makeMsg({ role: 'assistant', content: 'a', visibility: 'internal', threadId: 't1' }),
      makeMsg({ role: 'assistant', content: 'b', visibility: 'internal', threadId: 't1' }),
    ];
    const { result } = renderHook(() => useRoomState(msgs, twoParticipants));
    act(() => {
      result.current.toggleThread('t1');
    });
    expect(result.current.collapsedThreads.has('t1')).toBe(false);
  });

  it('toggleThread re-collapses an expanded thread', () => {
    const msgs = [
      makeMsg({ role: 'assistant', content: 'a', visibility: 'internal', threadId: 't1' }),
      makeMsg({ role: 'assistant', content: 'b', visibility: 'internal', threadId: 't1' }),
    ];
    const { result } = renderHook(() => useRoomState(msgs, twoParticipants));
    act(() => {
      result.current.toggleThread('t1');
    }); // expand
    act(() => {
      result.current.toggleThread('t1');
    }); // collapse again
    expect(result.current.collapsedThreads.has('t1')).toBe(true);
  });

  it('single internal message does not form a thread group', () => {
    const msgs = [
      makeMsg({ role: 'assistant', content: 'a', visibility: 'internal', threadId: 't1' }),
    ];
    const { result } = renderHook(() => useRoomState(msgs, twoParticipants));
    expect(result.current.collapsedThreads.size).toBe(0);
  });
});
