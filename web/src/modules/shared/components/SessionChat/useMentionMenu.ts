import { useState, useCallback, useMemo, useRef } from 'react';
import type { FileTreeEntry } from '@/modules/volundr/models';
import type { RoomParticipant } from '@/modules/shared/hooks/useSkuldChat';
import { getAccessToken } from '@/modules/shared/api/client';

/**
 * Fuzzy match: every character in the pattern must appear in order in the target.
 * e.g. "brk" matches "broker.py", "svc" matches "service_manager.py"
 */
function fuzzyMatch(target: string, pattern: string): boolean {
  let ti = 0;
  for (let pi = 0; pi < pattern.length; pi++) {
    const ch = pattern[pi];
    const found = target.indexOf(ch, ti);
    if (found === -1) {
      return false;
    }
    ti = found + 1;
  }
  return true;
}

export type MentionItem =
  | { kind: 'file'; entry: FileTreeEntry; depth: number }
  | { kind: 'agent'; participant: RoomParticipant };

export type SelectedMention =
  | { kind: 'file'; entry: FileTreeEntry }
  | { kind: 'agent'; participant: RoomParticipant };

interface UseMentionMenuReturn {
  isOpen: boolean;
  filter: string;
  selectedIndex: number;
  items: MentionItem[];
  loading: boolean;
  mentions: SelectedMention[];
  handleKeyDown: (e: React.KeyboardEvent) => boolean;
  handleChange: (value: string, cursorPos: number) => void;
  selectItem: (item: MentionItem) => string;
  expandDirectory: (item: MentionItem) => void;
  removeMention: (id: string) => void;
  close: () => void;
}

/**
 * Build the base URL for Skuld API calls from either a chat endpoint or plain host.
 * Gateway-routed sessions use paths like /s/{id}/session — we strip the
 * trailing /session (or /api/session) and use the prefix for API calls.
 */
function buildApiBase(chatEndpoint: string | null, sessionHost: string | null): string | null {
  if (chatEndpoint) {
    try {
      const parsed = new URL(chatEndpoint);
      const protocol = parsed.protocol === 'wss:' ? 'https:' : 'http:';
      const basePath = parsed.pathname.replace(/\/(api\/)?session$/, '');
      return `${protocol}//${parsed.host}${basePath}`;
    } catch {
      // Relative path (e.g. /s/{id}/session) — resolve against current origin.
      const basePath = chatEndpoint.replace(/\/(api\/)?session$/, '');
      return `${window.location.origin}${basePath}`;
    }
  }
  if (sessionHost) {
    return `https://${sessionHost}`;
  }
  return null;
}

/**
 * Fetch files from the Skuld pod's /api/files endpoint.
 * Returns an empty array on any failure so the UI degrades gracefully.
 */
async function fetchFilesFromPod(apiBase: string, path?: string): Promise<FileTreeEntry[]> {
  try {
    const params = path ? `?path=${encodeURIComponent(path)}` : '';
    const headers: Record<string, string> = {};
    const token = getAccessToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    const response = await fetch(`${apiBase}/api/files${params}`, { headers });
    if (!response.ok) {
      return [];
    }
    const data = await response.json();
    return data.entries ?? [];
  } catch {
    return [];
  }
}

/**
 * Extract the @-mention trigger from text at the cursor position.
 * Returns the filter text after @ or null if no mention trigger is active.
 */
function extractMentionFilter(text: string, cursorPos: number): string | null {
  // Walk backwards from cursor to find the @ trigger
  const before = text.slice(0, cursorPos);
  const atIndex = before.lastIndexOf('@');
  if (atIndex === -1) {
    return null;
  }

  // @ must be at start or preceded by whitespace
  if (atIndex > 0 && !/\s/.test(before[atIndex - 1])) {
    return null;
  }

  const afterAt = before.slice(atIndex + 1);
  // Close menu if there's a space in the filter (selection already done)
  if (afterAt.includes(' ')) {
    return null;
  }

  return afterAt;
}

export function useMentionMenu(
  _sessionId: string | null,
  sessionHost: string | null = null,
  chatEndpoint: string | null = null,
  participants: ReadonlyMap<string, RoomParticipant> = new Map()
): UseMentionMenuReturn {
  const apiBase = useMemo(
    () => buildApiBase(chatEndpoint, sessionHost),
    [chatEndpoint, sessionHost]
  );
  const [isOpen, setIsOpen] = useState(false);
  const [filter, setFilter] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [fileItems, setFileItems] = useState<MentionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [mentions, setMentions] = useState<SelectedMention[]>([]);

  // Track expanded directories to avoid re-fetching
  const expandedDirsRef = useRef<Set<string>>(new Set());
  // Track the cursor position for the @ trigger
  const triggerPosRef = useRef<number>(-1);

  // Build agent items from participants (ravn type only)
  const agentItems = useMemo<Array<{ kind: 'agent'; participant: RoomParticipant }>>(() => {
    const result: Array<{ kind: 'agent'; participant: RoomParticipant }> = [];
    for (const [, participant] of participants) {
      if (participant.participantType === 'ravn') {
        result.push({ kind: 'agent', participant });
      }
    }
    return result;
  }, [participants]);

  const fetchRootFiles = useCallback(async () => {
    if (!apiBase) {
      return;
    }
    setLoading(true);
    try {
      const entries = await fetchFilesFromPod(apiBase);
      const rootItems: MentionItem[] = entries.map(e => ({ kind: 'file', entry: e, depth: 0 }));
      setFileItems(rootItems);
      expandedDirsRef.current = new Set();
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  const close = useCallback(() => {
    setIsOpen(false);
    setFilter('');
    setSelectedIndex(0);
    triggerPosRef.current = -1;
  }, []);

  const handleChange = useCallback(
    (value: string, cursorPos: number) => {
      const mentionFilter = extractMentionFilter(value, cursorPos);

      if (mentionFilter !== null) {
        if (!isOpen) {
          triggerPosRef.current = cursorPos - mentionFilter.length - 1;
          setIsOpen(true);
          fetchRootFiles();
        }
        setFilter(mentionFilter);
        setSelectedIndex(0);
        return;
      }

      if (isOpen) {
        close();
      }
    },
    [isOpen, close, fetchRootFiles]
  );

  const filteredItems = useMemo(() => {
    const lower = filter.toLowerCase();

    // Filter agent items by persona name
    const filteredAgents = agentItems.filter(
      item => !lower || fuzzyMatch(item.participant.persona.toLowerCase(), lower)
    );

    if (!lower) {
      return [...filteredAgents, ...fileItems];
    }

    // If filter contains a slash, match against full path
    const matchPath = lower.includes('/');

    // Build set of expanded dirs so we always show their parents
    const matchingPaths = new Set<string>();
    for (const item of fileItems) {
      if (item.kind !== 'file') continue;
      const target = matchPath ? item.entry.path.toLowerCase() : item.entry.name.toLowerCase();
      if (fuzzyMatch(target, lower)) {
        matchingPaths.add(item.entry.path);
        // Keep parent directories visible
        let parent = item.entry.path;
        while (parent.includes('/')) {
          parent = parent.slice(0, parent.lastIndexOf('/'));
          matchingPaths.add(parent);
        }
      }
    }

    const filteredFiles = fileItems.filter(
      item => item.kind === 'file' && matchingPaths.has(item.entry.path)
    );

    return [...filteredAgents, ...filteredFiles];
  }, [agentItems, fileItems, filter]);

  const expandDirectory = useCallback(
    async (item: MentionItem) => {
      if (item.kind !== 'file' || !apiBase || item.entry.type !== 'directory') {
        return;
      }

      const dirPath = item.entry.path;

      // If already expanded, collapse it
      if (expandedDirsRef.current.has(dirPath)) {
        expandedDirsRef.current.delete(dirPath);
        // Remove children from items
        setFileItems(prev =>
          prev.filter(i => i.kind !== 'file' || !i.entry.path.startsWith(dirPath + '/'))
        );
        return;
      }

      setLoading(true);
      try {
        const children = await fetchFilesFromPod(apiBase, dirPath);
        const childItems: MentionItem[] = children.map(e => ({
          kind: 'file',
          entry: e,
          depth: item.depth + 1,
        }));

        expandedDirsRef.current.add(dirPath);

        // Insert children right after the parent directory
        setFileItems(prev => {
          const parentIndex = prev.findIndex(i => i.kind === 'file' && i.entry.path === dirPath);
          if (parentIndex === -1) {
            return prev;
          }
          const result = [...prev];
          result.splice(parentIndex + 1, 0, ...childItems);
          return result;
        });
      } finally {
        setLoading(false);
      }
    },
    [apiBase]
  );

  const selectItem = useCallback(
    (item: MentionItem): string => {
      if (item.kind === 'agent') {
        setMentions(prev => {
          if (
            prev.some(m => m.kind === 'agent' && m.participant.peerId === item.participant.peerId)
          ) {
            return prev;
          }
          return [...prev, { kind: 'agent', participant: item.participant }];
        });
        close();
        return item.participant.persona;
      }
      // file
      setMentions(prev => {
        if (prev.some(m => m.kind === 'file' && m.entry.path === item.entry.path)) {
          return prev;
        }
        return [...prev, { kind: 'file', entry: item.entry }];
      });
      close();
      return item.entry.path;
    },
    [close]
  );

  const removeMention = useCallback((id: string) => {
    setMentions(prev =>
      prev.filter(m => {
        if (m.kind === 'file') return m.entry.path !== id;
        return m.participant.peerId !== id;
      })
    );
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent): boolean => {
      if (!isOpen) {
        return false;
      }

      if (e.key === 'Escape') {
        e.preventDefault();
        close();
        return true;
      }

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex(prev => (prev + 1) % Math.max(filteredItems.length, 1));
        return true;
      }

      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex(
          prev => (prev - 1 + filteredItems.length) % Math.max(filteredItems.length, 1)
        );
        return true;
      }

      if (e.key === 'Tab' || e.key === 'ArrowRight') {
        if (filteredItems.length > 0) {
          const selected = filteredItems[selectedIndex];
          if (selected?.kind === 'file' && selected.entry.type === 'directory') {
            e.preventDefault();
            expandDirectory(selected);
            return true;
          }
        }
      }

      if (e.key === 'Enter') {
        if (filteredItems.length > 0) {
          e.preventDefault();
          const selected = filteredItems[selectedIndex];
          if (selected?.kind === 'file' && selected.entry.type === 'directory') {
            expandDirectory(selected);
          }
          return true;
        }
      }

      return false;
    },
    [isOpen, filteredItems, selectedIndex, close, expandDirectory]
  );

  return {
    isOpen,
    filter,
    selectedIndex,
    items: filteredItems,
    loading,
    mentions,
    handleKeyDown,
    handleChange,
    selectItem,
    expandDirectory,
    removeMention,
    close,
  };
}
