import { useState, useMemo, useCallback } from 'react';
import type { SkuldChatMessage, SkuldChatMessagePart } from './useSkuldChat';
import type { RoomParticipant } from './useSkuldChat';

export interface ThreadGroupData {
  threadId: string;
  messages: readonly SkuldChatMessage[];
  participants: ReadonlySet<string>;
  startTime: Date;
  endTime: Date;
}

export interface UseRoomStateReturn {
  participants: ReadonlyMap<string, RoomParticipant>;
  isRoomMode: boolean;
  activeFilter: string;
  setActiveFilter: (f: string) => void;
  showInternal: boolean;
  setShowInternal: (visible: boolean) => void;
  toggleInternal: () => void;
  filteredMessages: readonly SkuldChatMessage[];
  visibleMessages: readonly SkuldChatMessage[];
  collapsedThreads: ReadonlySet<string>;
  toggleThread: (threadId: string) => void;
  threadGroups: ReadonlyMap<string, ThreadGroupData>;
}

const FILTER_ALL = 'all';
const INTERNAL_PART_TYPES: ReadonlySet<SkuldChatMessagePart['type']> = new Set([
  'tool_use',
  'tool_result',
]);

function isVisibleMessage(msg: SkuldChatMessage): boolean {
  if (msg.role === 'system') return false;
  if (msg.role === 'assistant' && msg.status === 'complete' && !msg.content.trim()) return false;
  return true;
}

function stripInternalParts(msg: SkuldChatMessage): SkuldChatMessage | null {
  if (!msg.parts || msg.parts.length === 0) return msg;
  const kept = msg.parts.filter(p => !INTERNAL_PART_TYPES.has(p.type));
  if (kept.length === msg.parts.length) return msg;
  if (kept.length === 0 && !msg.content.trim()) return null;
  return { ...msg, parts: kept };
}

export function useRoomState(
  messages: readonly SkuldChatMessage[],
  participants: ReadonlyMap<string, RoomParticipant>
): UseRoomStateReturn {
  const [activeFilter, setActiveFilter] = useState<string>(FILTER_ALL);
  const [showInternal, setShowInternal] = useState(false);
  const [expandedThreads, setExpandedThreads] = useState<ReadonlySet<string>>(new Set());

  const isRoomMode = participants.size > 1;

  const toggleInternal = useCallback(() => {
    setShowInternal(prev => !prev);
  }, []);

  const toggleThread = useCallback((threadId: string) => {
    setExpandedThreads(prev => {
      const next = new Set(prev);
      if (next.has(threadId)) {
        next.delete(threadId);
      } else {
        next.add(threadId);
      }
      return next;
    });
  }, []);

  const filteredMessages = useMemo<readonly SkuldChatMessage[]>(() => {
    const out: SkuldChatMessage[] = [];
    for (const msg of messages) {
      if (!showInternal && msg.visibility === 'internal') continue;
      if (isRoomMode && activeFilter !== FILTER_ALL && msg.participantId !== activeFilter) {
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

  // Remove system messages and empty assistant messages — same filter SessionChat
  // applies before rendering. threadGroups uses this array so both grouping
  // algorithms always operate on the same input.
  const visibleMessages = useMemo(
    () => filteredMessages.filter(isVisibleMessage),
    [filteredMessages]
  );

  const threadGroups = useMemo((): ReadonlyMap<string, ThreadGroupData> => {
    if (!isRoomMode || !showInternal) return new Map();

    const groups = new Map<string, ThreadGroupData>();
    let i = 0;
    while (i < visibleMessages.length) {
      const msg = visibleMessages[i];
      if (msg.visibility === 'internal' && msg.threadId) {
        const threadId = msg.threadId;
        const threadMsgs: SkuldChatMessage[] = [msg];
        let j = i + 1;
        while (j < visibleMessages.length) {
          const next = visibleMessages[j];
          if (next.visibility === 'internal' && next.threadId === threadId) {
            threadMsgs.push(next);
            j++;
          } else {
            break;
          }
        }
        if (threadMsgs.length > 1) {
          const participantIds = new Set<string>(
            threadMsgs.map(m => m.participantId).filter((id): id is string => id !== undefined)
          );
          groups.set(threadId, {
            threadId,
            messages: threadMsgs,
            participants: participantIds,
            startTime: threadMsgs[0].createdAt,
            endTime: threadMsgs[threadMsgs.length - 1].createdAt,
          });
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
    for (const threadId of threadGroups.keys()) {
      if (!expandedThreads.has(threadId)) {
        collapsed.add(threadId);
      }
    }
    return collapsed;
  }, [threadGroups, expandedThreads]);

  return {
    participants,
    isRoomMode,
    activeFilter,
    setActiveFilter,
    showInternal,
    setShowInternal,
    toggleInternal,
    filteredMessages,
    visibleMessages,
    collapsedThreads,
    toggleThread,
    threadGroups,
  };
}
