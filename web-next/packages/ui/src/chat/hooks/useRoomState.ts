import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import type { ChatMessage, RoomParticipant } from '../types';

export interface UseRoomStateReturn {
  isRoomMode: boolean;
  activeFilter: string;
  setActiveFilter: (f: string) => void;
  showInternal: boolean;
  toggleInternal: () => void;
  visibleMessages: readonly ChatMessage[];
  collapsedThreads: ReadonlySet<string>;
  toggleThread: (threadId: string) => void;
}

const FILTER_ALL = 'all';

function isVisibleMessage(msg: ChatMessage): boolean {
  if (msg.metadata?.messageType === 'system') return false;
  if (
    msg.role === 'assistant' &&
    msg.status === 'done' &&
    !msg.content.trim() &&
    !(msg.parts && msg.parts.length > 0)
  ) {
    return false;
  }
  return true;
}

/**
 * Manages filter/visibility state for room-mode sessions with multiple participants.
 */
export function useRoomState(
  messages: readonly ChatMessage[],
  participants: ReadonlyMap<string, RoomParticipant>,
): UseRoomStateReturn {
  const [activeFilter, setActiveFilter] = useState<string>(FILTER_ALL);
  const [showInternal, setShowInternal] = useState(false);
  const [expandedThreads, setExpandedThreads] = useState<ReadonlySet<string>>(new Set());
  const initializedRoomModeRef = useRef(false);

  const isRoomMode = participants.size > 1;

  useEffect(() => {
    if (isRoomMode && !initializedRoomModeRef.current) {
      initializedRoomModeRef.current = true;
      setShowInternal(true);
      return;
    }
    if (!isRoomMode) {
      initializedRoomModeRef.current = false;
    }
  }, [isRoomMode]);

  const toggleInternal = useCallback(() => {
    setShowInternal((prev) => !prev);
  }, []);

  const toggleThread = useCallback((threadId: string) => {
    setExpandedThreads((prev) => {
      const next = new Set(prev);
      if (next.has(threadId)) {
        next.delete(threadId);
      } else {
        next.add(threadId);
      }
      return next;
    });
  }, []);

  const filteredMessages = useMemo(() => {
    if (!isRoomMode) return messages;
    return messages.filter((msg) => {
      if (!showInternal && msg.visibility === 'internal') return false;
      if (activeFilter !== FILTER_ALL && msg.participant?.peerId !== activeFilter) return false;
      return true;
    });
  }, [messages, isRoomMode, activeFilter, showInternal]);

  const visibleMessages = useMemo(
    () => filteredMessages.filter(isVisibleMessage),
    [filteredMessages],
  );

  const threadGroups = useMemo((): ReadonlySet<string> => {
    if (!isRoomMode || !showInternal) return new Set();
    const groups = new Set<string>();
    let i = 0;
    while (i < visibleMessages.length) {
      const msg = visibleMessages[i];
      if (!msg) {
        i++;
        continue;
      }
      if (msg.visibility === 'internal' && msg.threadId) {
        const threadId = msg.threadId;
        let count = 1;
        let j = i + 1;
        while (j < visibleMessages.length) {
          const next = visibleMessages[j];
          if (next && next.visibility === 'internal' && next.threadId === threadId) {
            count++;
            j++;
          } else {
            break;
          }
        }
        if (count > 1) {
          groups.add(threadId);
          i = j;
          continue;
        }
      }
      i++;
    }
    return groups;
  }, [visibleMessages, isRoomMode, showInternal]);

  const collapsedThreads = useMemo((): ReadonlySet<string> => {
    const collapsed = new Set<string>();
    for (const threadId of threadGroups) {
      if (!expandedThreads.has(threadId)) {
        collapsed.add(threadId);
      }
    }
    return collapsed;
  }, [threadGroups, expandedThreads]);

  return {
    isRoomMode,
    activeFilter,
    setActiveFilter,
    showInternal,
    toggleInternal,
    visibleMessages,
    collapsedThreads,
    toggleThread,
  };
}
