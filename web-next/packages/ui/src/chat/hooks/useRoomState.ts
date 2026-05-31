import { useState, useMemo, useCallback } from 'react';
import type { ChatMessage, ChatMessagePart, RoomParticipant } from '../types';

export interface UseRoomStateReturn {
  isRoomMode: boolean;
  activeFilter: string;
  setActiveFilter: (f: string) => void;
  showInternal: boolean;
  setShowInternal: (visible: boolean) => void;
  toggleInternal: () => void;
  visibleMessages: readonly ChatMessage[];
  collapsedThreads: ReadonlySet<string>;
  toggleThread: (threadId: string) => void;
}

const FILTER_ALL = 'all';
const INTERNAL_PART_TYPES = new Set<ChatMessagePart['type']>(['tool_use', 'tool_result']);

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

function stripInternalParts(msg: ChatMessage): ChatMessage | null {
  if (!msg.parts || msg.parts.length === 0) {
    return msg;
  }
  const kept = msg.parts.filter((p) => !INTERNAL_PART_TYPES.has(p.type));
  if (kept.length === msg.parts.length) {
    return msg;
  }
  // If the message had only tool blocks and no text content, drop it entirely
  // so the chat doesn't render an empty assistant bubble.
  if (kept.length === 0 && !msg.content.trim()) {
    return null;
  }
  return { ...msg, parts: kept };
}

/**
 * Manages filter/visibility state for chat sessions.
 *
 * `showInternal` controls visibility of two kinds of "internal" content:
 *  - Room-mode peer/delegation messages with ``visibility === 'internal'``.
 *  - Tool calls (``tool_use``) and tool results (``tool_result``) inside
 *    any assistant message — when off, these blocks are stripped from
 *    ``parts`` and the assistant message is hidden if nothing else remains.
 */
export function useRoomState(
  messages: readonly ChatMessage[],
  participants: ReadonlyMap<string, RoomParticipant>,
): UseRoomStateReturn {
  const [activeFilter, setActiveFilter] = useState<string>(FILTER_ALL);
  const [showInternal, setShowInternal] = useState(false);
  const [expandedThreads, setExpandedThreads] = useState<ReadonlySet<string>>(new Set());

  const isRoomMode = participants.size > 1;

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

  const filteredMessages = useMemo<readonly ChatMessage[]>(() => {
    const out: ChatMessage[] = [];
    for (const msg of messages) {
      if (isRoomMode && !showInternal && msg.visibility === 'internal') continue;
      if (isRoomMode && activeFilter !== FILTER_ALL && msg.participant?.peerId !== activeFilter) {
        continue;
      }
      if (showInternal) {
        out.push(msg);
        continue;
      }
      const stripped = stripInternalParts(msg);
      if (stripped) {
        out.push(stripped);
      }
    }
    return out;
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
    setShowInternal,
    toggleInternal,
    visibleMessages,
    collapsedThreads,
    toggleThread,
  };
}
