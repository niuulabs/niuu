import { renderHook, act } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { useRoomState } from './useRoomState';
import type { SkuldChatMessage } from './useSkuldChat';
import type { RoomParticipant } from './useSkuldChat';

function makeParticipant(overrides: Partial<RoomParticipant> = {}): RoomParticipant {
  return {
    peerId: 'peer-1',
    persona: 'Ravn-A',
    color: 'p1',
    participantType: 'ravn',
    status: 'idle',
    joinedAt: new Date(),
    ...overrides,
  };
}

function makeMessage(overrides: Partial<SkuldChatMessage> = {}): SkuldChatMessage {
  return {
    id: `msg-${Math.random()}`,
    role: 'assistant',
    content: 'hello',
    createdAt: new Date(),
    status: 'complete',
    ...overrides,
  };
}

function makeParticipantsMap(
  participants: RoomParticipant[]
): ReadonlyMap<string, RoomParticipant> {
  return new Map(participants.map(p => [p.peerId, p]));
}

describe('useRoomState', () => {
  describe('isRoomMode', () => {
    it('is false when 0 participants', () => {
      const { result } = renderHook(() => useRoomState([], new Map()));
      expect(result.current.isRoomMode).toBe(false);
    });

    it('is false when 1 participant', () => {
      const participants = makeParticipantsMap([makeParticipant()]);
      const { result } = renderHook(() => useRoomState([], participants));
      expect(result.current.isRoomMode).toBe(false);
    });

    it('is true when 2+ participants', () => {
      const participants = makeParticipantsMap([
        makeParticipant({ peerId: 'peer-1', persona: 'Ravn-A' }),
        makeParticipant({ peerId: 'peer-2', persona: 'Ravn-B' }),
      ]);
      const { result } = renderHook(() => useRoomState([], participants));
      expect(result.current.isRoomMode).toBe(true);
    });
  });

  describe('filtering by participant', () => {
    const participants = makeParticipantsMap([
      makeParticipant({ peerId: 'peer-1', persona: 'Ravn-A' }),
      makeParticipant({ peerId: 'peer-2', persona: 'Ravn-B' }),
    ]);
    const messages = [
      makeMessage({ id: 'msg-1', participantId: 'peer-1' }),
      makeMessage({ id: 'msg-2', participantId: 'peer-2' }),
      makeMessage({ id: 'msg-3', participantId: 'peer-1' }),
    ];

    it('returns all messages when activeFilter is "all"', () => {
      const { result } = renderHook(() => useRoomState(messages, participants));
      expect(result.current.filteredMessages).toHaveLength(3);
    });

    it('filters to specific participant', () => {
      const { result } = renderHook(() => useRoomState(messages, participants));

      act(() => {
        result.current.setActiveFilter('peer-1');
      });

      expect(result.current.filteredMessages).toHaveLength(2);
      expect(result.current.filteredMessages.every(m => m.participantId === 'peer-1')).toBe(true);
    });

    it('filters to another participant', () => {
      const { result } = renderHook(() => useRoomState(messages, participants));

      act(() => {
        result.current.setActiveFilter('peer-2');
      });

      expect(result.current.filteredMessages).toHaveLength(1);
      expect(result.current.filteredMessages[0].id).toBe('msg-2');
    });

    it('restores all messages when filter set back to "all"', () => {
      const { result } = renderHook(() => useRoomState(messages, participants));

      act(() => {
        result.current.setActiveFilter('peer-1');
      });
      act(() => {
        result.current.setActiveFilter('all');
      });

      expect(result.current.filteredMessages).toHaveLength(3);
    });
  });

  describe('internal message toggle', () => {
    const participants = makeParticipantsMap([
      makeParticipant({ peerId: 'peer-1', persona: 'Ravn-A' }),
      makeParticipant({ peerId: 'peer-2', persona: 'Ravn-B' }),
    ]);
    const messages = [
      makeMessage({ id: 'msg-1', visibility: 'public' }),
      makeMessage({ id: 'msg-2', visibility: 'internal' }),
      makeMessage({ id: 'msg-3' }),
    ];

    it('hides internal messages by default', () => {
      const { result } = renderHook(() => useRoomState(messages, participants));
      const ids = result.current.filteredMessages.map(m => m.id);
      expect(ids).not.toContain('msg-2');
      expect(ids).toContain('msg-1');
      expect(ids).toContain('msg-3');
    });

    it('shows internal messages after toggle', () => {
      const { result } = renderHook(() => useRoomState(messages, participants));

      act(() => {
        result.current.toggleInternal();
      });

      const ids = result.current.filteredMessages.map(m => m.id);
      expect(ids).toContain('msg-2');
    });

    it('hides internal again after second toggle', () => {
      const { result } = renderHook(() => useRoomState(messages, participants));

      act(() => {
        result.current.toggleInternal();
      });
      act(() => {
        result.current.toggleInternal();
      });

      const ids = result.current.filteredMessages.map(m => m.id);
      expect(ids).not.toContain('msg-2');
    });

    it('showInternal starts false', () => {
      const { result } = renderHook(() => useRoomState([], participants));
      expect(result.current.showInternal).toBe(false);
    });

    it('showInternal becomes true after toggle', () => {
      const { result } = renderHook(() => useRoomState([], participants));
      act(() => {
        result.current.toggleInternal();
      });
      expect(result.current.showInternal).toBe(true);
    });
  });

  describe('passthrough in single-agent mode', () => {
    const singleParticipant = makeParticipantsMap([makeParticipant()]);
    const messages = [
      makeMessage({ id: 'msg-1', visibility: 'internal' }),
      makeMessage({ id: 'msg-2', visibility: 'public' }),
    ];

    it('returns all messages unfiltered when not room mode', () => {
      const { result } = renderHook(() => useRoomState(messages, singleParticipant));
      expect(result.current.filteredMessages).toHaveLength(2);
    });
  });

  describe('visibleMessages', () => {
    const participants = makeParticipantsMap([
      makeParticipant({ peerId: 'peer-1', persona: 'Ravn-A' }),
      makeParticipant({ peerId: 'peer-2', persona: 'Ravn-B' }),
    ]);

    it('excludes system role messages', () => {
      const messages = [
        makeMessage({ id: 'msg-1', role: 'user' }),
        makeMessage({ id: 'msg-2', role: 'system' }),
        makeMessage({ id: 'msg-3', role: 'assistant' }),
      ];
      const { result } = renderHook(() => useRoomState(messages, participants));
      const ids = result.current.visibleMessages.map(m => m.id);
      expect(ids).not.toContain('msg-2');
      expect(ids).toContain('msg-1');
      expect(ids).toContain('msg-3');
    });

    it('excludes empty complete assistant messages', () => {
      const messages = [
        makeMessage({ id: 'msg-1', role: 'assistant', status: 'complete', content: '' }),
        makeMessage({ id: 'msg-2', role: 'assistant', status: 'complete', content: '   ' }),
        makeMessage({ id: 'msg-3', role: 'assistant', status: 'complete', content: 'hello' }),
      ];
      const { result } = renderHook(() => useRoomState(messages, participants));
      const ids = result.current.visibleMessages.map(m => m.id);
      expect(ids).not.toContain('msg-1');
      expect(ids).not.toContain('msg-2');
      expect(ids).toContain('msg-3');
    });

    it('includes running assistant messages even with empty content', () => {
      const messages = [
        makeMessage({ id: 'msg-1', role: 'assistant', status: 'running', content: '' }),
      ];
      const { result } = renderHook(() => useRoomState(messages, participants));
      expect(result.current.visibleMessages.map(m => m.id)).toContain('msg-1');
    });

    it('uses visibleMessages as basis for threadGroups so collapsed state matches render', () => {
      // A system message between two internal messages of the same thread breaks
      // consecutive grouping in filteredMessages but NOT in visibleMessages.
      // threadGroups must use visibleMessages so collapsedThreads matches what renders.
      const messages = [
        makeMessage({
          id: 'a',
          role: 'assistant',
          visibility: 'internal',
          threadId: 'tid-1',
          content: 'a',
        }),
        makeMessage({ id: 'sys', role: 'system', content: 'system event' }),
        makeMessage({
          id: 'b',
          role: 'assistant',
          visibility: 'internal',
          threadId: 'tid-1',
          content: 'b',
        }),
      ];
      const { result } = renderHook(() => useRoomState(messages, participants));

      act(() => {
        result.current.toggleInternal();
      });

      // visibleMessages has [a, b] consecutive (system filtered out), so the thread groups
      expect(result.current.threadGroups.has('tid-1')).toBe(true);
      // and is collapsed by default
      expect(result.current.collapsedThreads.has('tid-1')).toBe(true);
    });
  });

  describe('activeFilter state', () => {
    it('defaults to "all"', () => {
      const { result } = renderHook(() => useRoomState([], new Map()));
      expect(result.current.activeFilter).toBe('all');
    });

    it('updates via setActiveFilter', () => {
      const { result } = renderHook(() => useRoomState([], new Map()));
      act(() => {
        result.current.setActiveFilter('peer-42');
      });
      expect(result.current.activeFilter).toBe('peer-42');
    });
  });

  describe('threadGroups', () => {
    const participants = makeParticipantsMap([
      makeParticipant({ peerId: 'peer-1', persona: 'Ravn-A' }),
      makeParticipant({ peerId: 'peer-2', persona: 'Ravn-B' }),
    ]);

    it('returns empty map when not room mode', () => {
      const singleParticipant = makeParticipantsMap([makeParticipant()]);
      const messages = [
        makeMessage({ visibility: 'internal', threadId: 'tid-1' }),
        makeMessage({ visibility: 'internal', threadId: 'tid-1' }),
      ];
      const { result } = renderHook(() => useRoomState(messages, singleParticipant));
      expect(result.current.threadGroups.size).toBe(0);
    });

    it('returns empty map when showInternal is false', () => {
      const messages = [
        makeMessage({ visibility: 'internal', threadId: 'tid-1' }),
        makeMessage({ visibility: 'internal', threadId: 'tid-1' }),
      ];
      const { result } = renderHook(() => useRoomState(messages, participants));
      expect(result.current.threadGroups.size).toBe(0);
    });

    it('groups consecutive internal messages with same threadId', () => {
      const t0 = new Date('2024-01-01T12:03:00');
      const t1 = new Date('2024-01-01T12:07:00');
      const messages = [
        makeMessage({
          id: 'a',
          visibility: 'internal',
          threadId: 'tid-1',
          createdAt: t0,
          participantId: 'peer-1',
        }),
        makeMessage({
          id: 'b',
          visibility: 'internal',
          threadId: 'tid-1',
          createdAt: t1,
          participantId: 'peer-2',
        }),
      ];
      const { result } = renderHook(() => useRoomState(messages, participants));

      act(() => {
        result.current.toggleInternal();
      });

      const group = result.current.threadGroups.get('tid-1');
      expect(group).toBeDefined();
      expect(group!.messages).toHaveLength(2);
      expect(group!.startTime).toEqual(t0);
      expect(group!.endTime).toEqual(t1);
      expect(group!.participants.has('peer-1')).toBe(true);
      expect(group!.participants.has('peer-2')).toBe(true);
    });

    it('does not group a single internal message', () => {
      const messages = [makeMessage({ visibility: 'internal', threadId: 'tid-1' })];
      const { result } = renderHook(() => useRoomState(messages, participants));

      act(() => {
        result.current.toggleInternal();
      });

      expect(result.current.threadGroups.size).toBe(0);
    });

    it('does not group non-consecutive messages with same threadId', () => {
      const messages = [
        makeMessage({ id: 'a', visibility: 'internal', threadId: 'tid-1' }),
        makeMessage({ id: 'b', visibility: 'public', threadId: 'tid-1' }),
        makeMessage({ id: 'c', visibility: 'internal', threadId: 'tid-1' }),
      ];
      const { result } = renderHook(() => useRoomState(messages, participants));

      act(() => {
        result.current.toggleInternal();
      });

      expect(result.current.threadGroups.size).toBe(0);
    });

    it('handles interleaved threads separately', () => {
      const messages = [
        makeMessage({ id: 'a', visibility: 'internal', threadId: 'tid-1' }),
        makeMessage({ id: 'b', visibility: 'internal', threadId: 'tid-1' }),
        makeMessage({ id: 'c', visibility: 'public' }),
        makeMessage({ id: 'd', visibility: 'internal', threadId: 'tid-2' }),
        makeMessage({ id: 'e', visibility: 'internal', threadId: 'tid-2' }),
      ];
      const { result } = renderHook(() => useRoomState(messages, participants));

      act(() => {
        result.current.toggleInternal();
      });

      expect(result.current.threadGroups.has('tid-1')).toBe(true);
      expect(result.current.threadGroups.has('tid-2')).toBe(true);
      expect(result.current.threadGroups.size).toBe(2);
    });

    it('does not group non-internal messages even if they share threadId', () => {
      const messages = [
        makeMessage({ id: 'a', visibility: 'public', threadId: 'tid-1' }),
        makeMessage({ id: 'b', visibility: 'public', threadId: 'tid-1' }),
      ];
      const { result } = renderHook(() => useRoomState(messages, participants));

      act(() => {
        result.current.toggleInternal();
      });

      expect(result.current.threadGroups.size).toBe(0);
    });
  });

  describe('collapsedThreads', () => {
    const participants = makeParticipantsMap([
      makeParticipant({ peerId: 'peer-1', persona: 'Ravn-A' }),
      makeParticipant({ peerId: 'peer-2', persona: 'Ravn-B' }),
    ]);

    function makeThreadMessages(threadId: string) {
      return [
        makeMessage({ id: `${threadId}-a`, visibility: 'internal', threadId }),
        makeMessage({ id: `${threadId}-b`, visibility: 'internal', threadId }),
      ];
    }

    it('collapses all threads by default', () => {
      const messages = makeThreadMessages('tid-1');
      const { result } = renderHook(() => useRoomState(messages, participants));

      act(() => {
        result.current.toggleInternal();
      });

      expect(result.current.collapsedThreads.has('tid-1')).toBe(true);
    });

    it('toggleThread expands a collapsed thread', () => {
      const messages = makeThreadMessages('tid-1');
      const { result } = renderHook(() => useRoomState(messages, participants));

      act(() => {
        result.current.toggleInternal();
      });
      act(() => {
        result.current.toggleThread('tid-1');
      });

      expect(result.current.collapsedThreads.has('tid-1')).toBe(false);
    });

    it('toggleThread collapses an expanded thread', () => {
      const messages = makeThreadMessages('tid-1');
      const { result } = renderHook(() => useRoomState(messages, participants));

      act(() => {
        result.current.toggleInternal();
      });
      act(() => {
        result.current.toggleThread('tid-1');
      });
      act(() => {
        result.current.toggleThread('tid-1');
      });

      expect(result.current.collapsedThreads.has('tid-1')).toBe(true);
    });

    it('only lists thread IDs that exist in threadGroups', () => {
      const messages = makeThreadMessages('tid-1');
      const { result } = renderHook(() => useRoomState(messages, participants));

      act(() => {
        result.current.toggleInternal();
      });

      // Only tid-1 exists as a group — collapsedThreads should only contain known IDs
      expect(result.current.collapsedThreads.size).toBe(1);
      expect(result.current.collapsedThreads.has('tid-1')).toBe(true);
    });
  });
});
