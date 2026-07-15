import { useCallback, useState } from 'react';
import type { KeyboardEvent } from 'react';
import type { RoomParticipant, FileEntry } from '../types';

export type SelectedMention =
  | { kind: 'file'; entry: FileEntry }
  | { kind: 'agent'; participant: RoomParticipant; eventType?: string };

export type MentionMenuItem =
  | { kind: 'file'; entry: FileEntry }
  | { kind: 'agent'; participant: RoomParticipant; eventType?: string };

export function mentionId(mention: SelectedMention): string {
  if (mention.kind === 'file') return mention.entry.path;
  return mention.eventType
    ? `${mention.participant.peerId}:${mention.eventType}`
    : mention.participant.peerId;
}

interface UseMentionMenuReturn {
  isOpen: boolean;
  items: MentionMenuItem[];
  selectedIndex: number;
  loading: boolean;
  mentions: SelectedMention[];
  handleChange: (value: string, cursorPos: number) => void;
  handleKeyDown: (e: KeyboardEvent) => boolean;
  selectItem: (item: MentionMenuItem) => string;
  removeMention: (id: string) => void;
  expandDirectory: (item: MentionMenuItem) => void;
  close: () => void;
}

const MAX_ITEMS = 20;

/**
 * Hook managing the @-mention autocomplete for files and agents.
 *
 * @param sessionId - optional session ID for file listing
 * @param sessionHost - optional host URL for file listing API
 * @param chatEndpoint - optional full chat endpoint URL
 * @param participants - available room participants
 * @param onFetchFiles - optional async callback to fetch files for a path
 */
export function useMentionMenu(
  _sessionId: string | null = null,
  sessionHost: string | null = null,
  chatEndpoint: string | null = null,
  participants: ReadonlyMap<string, RoomParticipant> = new Map(),
  onFetchFiles?: (path: string, apiBase: string) => Promise<FileEntry[]>,
  eventRouting = false,
): UseMentionMenuReturn {
  const [isOpen, setIsOpen] = useState(false);
  const [items, setItems] = useState<MentionMenuItem[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [mentions, setMentions] = useState<SelectedMention[]>([]);

  const buildApiBase = useCallback((): string | null => {
    if (chatEndpoint) {
      const url = new URL(chatEndpoint, 'http://localhost');
      const protocol =
        url.protocol === 'wss:' ? 'https:' : url.protocol === 'ws:' ? 'http:' : url.protocol;
      const pathname = url.pathname.replace(/\/chat$/, '').replace(/\/(api\/)?session$/, '');
      return `${protocol}//${url.host}${pathname}`;
    }
    if (sessionHost) return `http://${sessionHost}`;
    return null;
  }, [chatEndpoint, sessionHost]);

  const fetchFiles = useCallback(
    async (query: string) => {
      const apiBase = buildApiBase();
      if (!apiBase || !onFetchFiles) return;
      setLoading(true);
      try {
        const entries = await onFetchFiles(query, apiBase);
        const fileItems: MentionMenuItem[] = entries
          .slice(0, MAX_ITEMS)
          .map((entry) => ({ kind: 'file', entry }));
        setItems((prev) => {
          const agentItems = prev.filter((i) => i.kind === 'agent');
          return [...agentItems, ...fileItems];
        });
      } catch {
        // silently swallow
      } finally {
        setLoading(false);
      }
    },
    [buildApiBase, onFetchFiles],
  );

  const selectItem = useCallback((item: MentionMenuItem): string => {
    if (item.kind === 'file') {
      setMentions((prev) => {
        const already = prev.some((m) => m.kind === 'file' && m.entry.path === item.entry.path);
        if (already) return prev;
        return [...prev, { kind: 'file', entry: item.entry }];
      });
      setIsOpen(false);
      return item.entry.path;
    }
    setMentions((prev) => {
      const nextMention: SelectedMention = {
        kind: 'agent',
        participant: item.participant,
        eventType: item.eventType,
      };
      if (item.eventType) {
        return [...prev.filter((mention) => mention.kind !== 'agent'), nextMention];
      }
      const already = prev.some((mention) => mentionId(mention) === mentionId(nextMention));
      return already ? prev : [...prev, nextMention];
    });
    setIsOpen(false);
    return item.eventType ?? item.participant.displayName ?? item.participant.persona;
  }, []);

  const expandDirectory = useCallback(
    (item: MentionMenuItem) => {
      if (item.kind !== 'file' || item.entry.type !== 'directory') return;
      const path = item.entry.path;
      void fetchFiles(path.endsWith('/') ? path : `${path}/`);
    },
    [fetchFiles],
  );

  const handleChange = useCallback(
    (value: string, cursorPos: number) => {
      const textBeforeCursor = value.slice(0, cursorPos);
      const atIndex = textBeforeCursor.lastIndexOf('@');
      if (atIndex === -1) {
        setIsOpen(false);
        return;
      }
      const query = textBeforeCursor.slice(atIndex + 1);
      if (query.includes(' ')) {
        setIsOpen(false);
        return;
      }

      const queryLower = query.toLowerCase();
      const agentItems: MentionMenuItem[] = Array.from(participants.values())
        .filter((participant) => participant.participantType === 'ravn')
        .flatMap((participant): MentionMenuItem[] => {
          if (!eventRouting) return [{ kind: 'agent', participant }];
          return (participant.subscribesTo ?? []).map((eventType) => ({
            kind: 'agent',
            participant,
            eventType,
          }));
        })
        .filter((item) => {
          if (item.kind !== 'agent' || !queryLower) return true;
          return [item.participant.persona, item.participant.displayName, item.eventType]
            .filter((value): value is string => Boolean(value))
            .some((value) => value.toLowerCase().includes(queryLower));
        });

      setItems(agentItems.slice(0, MAX_ITEMS));
      setIsOpen(true);
      setSelectedIndex(0);

      // Fetch file entries if a file API is available
      void fetchFiles(query);
    },
    [eventRouting, participants, fetchFiles],
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent): boolean => {
      if (!isOpen) return false;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % Math.max(items.length, 1));
        return true;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex(
          (prev) => (prev - 1 + Math.max(items.length, 1)) % Math.max(items.length, 1),
        );
        return true;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setIsOpen(false);
        return true;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        const selected = items[selectedIndex];
        if (!selected) return false;
        e.preventDefault();
        if (selected.kind === 'file' && selected.entry.type === 'directory') {
          expandDirectory(selected);
        } else {
          selectItem(selected);
        }
        return true;
      }
      return false;
    },
    [isOpen, items, selectedIndex, selectItem, expandDirectory],
  );

  const removeMention = useCallback((id: string) => {
    setMentions((prev) => prev.filter((mention) => mentionId(mention) !== id));
  }, []);

  const close = useCallback(() => setIsOpen(false), []);

  return {
    isOpen,
    items,
    selectedIndex,
    loading,
    mentions,
    handleChange,
    handleKeyDown,
    selectItem,
    removeMention,
    expandDirectory,
    close,
  };
}
