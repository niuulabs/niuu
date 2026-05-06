import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useMentionMenu } from './useMentionMenu';
import type { RoomParticipant } from '@/modules/shared/hooks/useSkuldChat';

vi.mock('@/modules/shared/api/client', () => ({
  getAccessToken: vi.fn(() => null),
}));

// Mock global fetch for Skuld pod /api/files calls
const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

function mockFetchResponse(entries: Array<{ name: string; path: string; type: string }>) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ entries }),
  });
}

const DEFAULT_FILES = [
  { name: 'src', path: 'src', type: 'directory' },
  { name: 'package.json', path: 'package.json', type: 'file' },
  { name: 'README.md', path: 'README.md', type: 'file' },
];

function makeParticipant(overrides: Partial<RoomParticipant> = {}): RoomParticipant {
  return {
    peerId: 'peer-1',
    persona: 'Ravn-Alpha',
    color: '#a855f7',
    participantType: 'ravn',
    status: 'idle',
    joinedAt: new Date(),
    ...overrides,
  };
}

function makeParticipantsMap(
  participants: RoomParticipant[]
): ReadonlyMap<string, RoomParticipant> {
  return new Map(participants.map(p => [p.peerId, p]));
}

describe('useMentionMenu', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchResponse(DEFAULT_FILES);
  });

  it('starts closed with no mentions', () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));
    expect(result.current.isOpen).toBe(false);
    expect(result.current.filter).toBe('');
    expect(result.current.selectedIndex).toBe(0);
    expect(result.current.mentions).toEqual([]);
  });

  it('opens when input contains @ preceded by whitespace', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('hello @', 7);
    });

    expect(result.current.isOpen).toBe(true);
    expect(result.current.filter).toBe('');
  });

  it('opens when input starts with @', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    expect(result.current.isOpen).toBe(true);
  });

  it('does not open when @ is inside a word', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('email@', 6);
    });

    expect(result.current.isOpen).toBe(false);
  });

  it('filters items by name', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@pack', 5);
    });

    expect(result.current.isOpen).toBe(true);
    expect(result.current.filter).toBe('pack');
    // Filtered items should only include package.json
    const filtered = result.current.items;
    expect(filtered).toHaveLength(1);
    expect(filtered[0].kind).toBe('file');
    if (filtered[0].kind === 'file') {
      expect(filtered[0].entry.name).toBe('package.json');
    }
  });

  it('closes when @ trigger is followed by space', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@src ', 5);
    });

    expect(result.current.isOpen).toBe(false);
  });

  it('fetches files from Skuld pod when opening', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    expect(mockFetch).toHaveBeenCalledWith('https://pod.local/api/files', { headers: {} });
  });

  it('uses chatEndpoint to build gateway URL with path prefix', async () => {
    const { result } = renderHook(() =>
      useMentionMenu('session-1', null, 'wss://gateway.example.com/s/abc-123/session')
    );

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    expect(mockFetch).toHaveBeenCalledWith('https://gateway.example.com/s/abc-123/api/files', {
      headers: {},
    });
  });

  it('handles chatEndpoint with /api/session suffix', async () => {
    const { result } = renderHook(() =>
      useMentionMenu('session-1', null, 'wss://gateway.example.com/s/abc-123/api/session')
    );

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    expect(mockFetch).toHaveBeenCalledWith('https://gateway.example.com/s/abc-123/api/files', {
      headers: {},
    });
  });

  it('prefers chatEndpoint over sessionHost', async () => {
    const { result } = renderHook(() =>
      useMentionMenu('session-1', 'pod.local', 'wss://gateway.example.com/s/abc-123/session')
    );

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    expect(mockFetch).toHaveBeenCalledWith('https://gateway.example.com/s/abc-123/api/files', {
      headers: {},
    });
  });

  it('includes auth token in fetch headers when available', async () => {
    const { getAccessToken } = await import('@/modules/shared/api/client');
    vi.mocked(getAccessToken).mockReturnValue('test-token');

    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    expect(mockFetch).toHaveBeenCalledWith('https://pod.local/api/files', {
      headers: { Authorization: 'Bearer test-token' },
    });

    vi.mocked(getAccessToken).mockReturnValue(null);
  });

  it('does not fetch files when sessionHost is null', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', null));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('returns empty items when fetch fails', async () => {
    mockFetch.mockReset();
    mockFetch.mockRejectedValueOnce(new Error('Network error'));

    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    expect(result.current.items).toHaveLength(0);
    // Should not crash
    expect(result.current.isOpen).toBe(true);
  });

  it('returns empty items when fetch returns non-ok', async () => {
    mockFetch.mockReset();
    mockFetch.mockResolvedValueOnce({ ok: false, status: 500 });

    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    expect(result.current.items).toHaveLength(0);
  });

  it('selectItem adds file entry to mentions and closes menu', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    // items[0] is src (dir), items[1] is package.json (file)
    const fileItem = result.current.items.find(
      i => i.kind === 'file' && i.entry.name === 'package.json'
    );
    expect(fileItem).toBeDefined();

    let selectedLabel = '';
    act(() => {
      selectedLabel = result.current.selectItem(fileItem!);
    });

    expect(selectedLabel).toBe('package.json');
    expect(result.current.isOpen).toBe(false);
    expect(result.current.mentions).toHaveLength(1);
    const mention = result.current.mentions[0];
    expect(mention.kind).toBe('file');
    if (mention.kind === 'file') {
      expect(mention.entry.path).toBe('package.json');
    }
  });

  it('selectItem does not add duplicate file mentions', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    const fileItem = result.current.items.find(
      i => i.kind === 'file' && i.entry.name === 'package.json'
    )!;

    act(() => {
      result.current.selectItem(fileItem);
    });

    // Re-open and select same item
    mockFetchResponse(DEFAULT_FILES);
    await act(async () => {
      result.current.handleChange('@', 1);
    });

    const sameItem = result.current.items.find(
      i => i.kind === 'file' && i.entry.name === 'package.json'
    )!;
    act(() => {
      result.current.selectItem(sameItem);
    });

    expect(result.current.mentions).toHaveLength(1);
  });

  it('removeMention removes file entry from mentions', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    const fileItem = result.current.items.find(
      i => i.kind === 'file' && i.entry.name === 'package.json'
    )!;

    act(() => {
      result.current.selectItem(fileItem);
    });

    expect(result.current.mentions).toHaveLength(1);

    act(() => {
      result.current.removeMention('package.json');
    });

    expect(result.current.mentions).toHaveLength(0);
  });

  it('close() resets menu state', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@pack', 5);
    });

    expect(result.current.isOpen).toBe(true);

    act(() => {
      result.current.close();
    });

    expect(result.current.isOpen).toBe(false);
    expect(result.current.filter).toBe('');
    expect(result.current.selectedIndex).toBe(0);
  });

  it('handleKeyDown returns false when menu is closed', () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    let handled = false;
    act(() => {
      handled = result.current.handleKeyDown({
        key: 'ArrowDown',
        preventDefault: () => {},
      } as React.KeyboardEvent);
    });

    expect(handled).toBe(false);
  });

  it('handleKeyDown Escape closes menu', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    let handled = false;
    act(() => {
      handled = result.current.handleKeyDown({
        key: 'Escape',
        preventDefault: () => {},
      } as React.KeyboardEvent);
    });

    expect(handled).toBe(true);
    expect(result.current.isOpen).toBe(false);
  });

  it('handleKeyDown ArrowDown cycles selection forward', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    act(() => {
      result.current.handleKeyDown({
        key: 'ArrowDown',
        preventDefault: () => {},
      } as React.KeyboardEvent);
    });

    expect(result.current.selectedIndex).toBe(1);
  });

  it('handleKeyDown ArrowUp wraps to last item', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    act(() => {
      result.current.handleKeyDown({
        key: 'ArrowUp',
        preventDefault: () => {},
      } as React.KeyboardEvent);
    });

    // Wraps to last item (index 2 for 3 items)
    expect(result.current.selectedIndex).toBe(2);
  });

  it('handleKeyDown Enter returns true when items available', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    let handled = false;
    act(() => {
      handled = result.current.handleKeyDown({
        key: 'Enter',
        preventDefault: () => {},
      } as React.KeyboardEvent);
    });

    expect(handled).toBe(true);
  });

  it('handleKeyDown Tab expands selected directory', async () => {
    mockFetch.mockReset();
    mockFetchResponse([
      { name: 'src', path: 'src', type: 'directory' },
      { name: 'package.json', path: 'package.json', type: 'file' },
    ]);
    mockFetchResponse([{ name: 'App.tsx', path: 'src/App.tsx', type: 'file' }]);

    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    // First item is src directory
    expect(result.current.items[0].kind).toBe('file');
    if (result.current.items[0].kind === 'file') {
      expect(result.current.items[0].entry.type).toBe('directory');
    }

    await act(async () => {
      result.current.handleKeyDown({
        key: 'Tab',
        preventDefault: () => {},
      } as React.KeyboardEvent);
    });

    expect(mockFetch).toHaveBeenCalledWith('https://pod.local/api/files?path=src', { headers: {} });
  });

  it('handleKeyDown returns false for unhandled keys', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    let handled = false;
    act(() => {
      handled = result.current.handleKeyDown({
        key: 'a',
        preventDefault: () => {},
      } as React.KeyboardEvent);
    });

    expect(handled).toBe(false);
  });

  it('expandDirectory loads children and inserts after parent', async () => {
    mockFetch.mockReset();
    mockFetchResponse([
      { name: 'src', path: 'src', type: 'directory' },
      { name: 'tests', path: 'tests', type: 'directory' },
    ]);
    mockFetchResponse([
      { name: 'App.tsx', path: 'src/App.tsx', type: 'file' },
      { name: 'main.tsx', path: 'src/main.tsx', type: 'file' },
    ]);

    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    expect(result.current.items).toHaveLength(2);

    await act(async () => {
      result.current.expandDirectory(result.current.items[0]); // expand src
    });

    expect(result.current.items).toHaveLength(4);
    expect(result.current.items[0].kind).toBe('file');
    if (result.current.items[0].kind === 'file') {
      expect(result.current.items[0].entry.name).toBe('src');
    }
    expect(result.current.items[1].kind).toBe('file');
    if (result.current.items[1].kind === 'file') {
      expect(result.current.items[1].entry.name).toBe('App.tsx');
      expect(result.current.items[1].depth).toBe(1);
    }
    expect(result.current.items[2].kind).toBe('file');
    if (result.current.items[2].kind === 'file') {
      expect(result.current.items[2].entry.name).toBe('main.tsx');
    }
  });

  it('expandDirectory collapses already expanded directory', async () => {
    mockFetch.mockReset();
    mockFetchResponse([{ name: 'src', path: 'src', type: 'directory' }]);
    mockFetchResponse([{ name: 'App.tsx', path: 'src/App.tsx', type: 'file' }]);

    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    // Expand
    await act(async () => {
      result.current.expandDirectory(result.current.items[0]);
    });

    expect(result.current.items).toHaveLength(2);

    // Collapse
    await act(async () => {
      result.current.expandDirectory(result.current.items[0]);
    });

    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0].kind).toBe('file');
    if (result.current.items[0].kind === 'file') {
      expect(result.current.items[0].entry.name).toBe('src');
    }
  });

  it('fuzzy matches file names (e.g. "pkg" matches "package.json")', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@pkg', 4);
    });

    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0].kind).toBe('file');
    if (result.current.items[0].kind === 'file') {
      expect(result.current.items[0].entry.name).toBe('package.json');
    }
  });

  it('fuzzy matches partial sequences (e.g. "rdm" matches "README.md")', async () => {
    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@rdm', 4);
    });

    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0].kind).toBe('file');
    if (result.current.items[0].kind === 'file') {
      expect(result.current.items[0].entry.name).toBe('README.md');
    }
  });

  it('keeps parent directories visible when children match filter', async () => {
    mockFetch.mockReset();
    mockFetchResponse([
      { name: 'src', path: 'src', type: 'directory' },
      { name: 'tests', path: 'tests', type: 'directory' },
    ]);
    mockFetchResponse([
      { name: 'App.tsx', path: 'src/App.tsx', type: 'file' },
      { name: 'main.tsx', path: 'src/main.tsx', type: 'file' },
    ]);

    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    // Expand src
    await act(async () => {
      result.current.expandDirectory(result.current.items[0]);
    });

    // Filter for "app" — should show src (parent) and App.tsx
    await act(async () => {
      result.current.handleChange('@app', 4);
    });

    const names = result.current.items.map(i =>
      i.kind === 'file' ? i.entry.name : i.participant.persona
    );
    expect(names).toContain('src');
    expect(names).toContain('App.tsx');
    expect(names).not.toContain('tests');
    expect(names).not.toContain('main.tsx');
  });

  it('matches full path when filter contains slash', async () => {
    mockFetch.mockReset();
    mockFetchResponse([{ name: 'src', path: 'src', type: 'directory' }]);
    mockFetchResponse([{ name: 'App.tsx', path: 'src/App.tsx', type: 'file' }]);

    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    await act(async () => {
      result.current.expandDirectory(result.current.items[0]);
    });

    await act(async () => {
      result.current.handleChange('@src/app', 8);
    });

    const names = result.current.items.map(i =>
      i.kind === 'file' ? i.entry.name : i.participant.persona
    );
    expect(names).toContain('src');
    expect(names).toContain('App.tsx');
  });

  it('Enter on directory expands it instead of selecting', async () => {
    mockFetch.mockReset();
    mockFetchResponse([
      { name: 'src', path: 'src', type: 'directory' },
      { name: 'file.txt', path: 'file.txt', type: 'file' },
    ]);
    mockFetchResponse([{ name: 'App.tsx', path: 'src/App.tsx', type: 'file' }]);

    const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local'));

    await act(async () => {
      result.current.handleChange('@', 1);
    });

    // First item is src directory, press Enter
    await act(async () => {
      result.current.handleKeyDown({
        key: 'Enter',
        preventDefault: () => {},
      } as React.KeyboardEvent);
    });

    // Menu should still be open (directory expanded, not selected)
    expect(result.current.isOpen).toBe(true);
    // Should have fetched children
    expect(mockFetch).toHaveBeenCalledWith('https://pod.local/api/files?path=src', { headers: {} });
  });

  // ── Agent mention tests ────────────────────────────────────────────────

  describe('with participants', () => {
    const alpha = makeParticipant({
      peerId: 'peer-alpha',
      persona: 'Ravn-Alpha',
      color: '#a855f7',
    });
    const beta = makeParticipant({ peerId: 'peer-beta', persona: 'Ravn-Beta', color: '#06b6d4' });
    const human = makeParticipant({
      peerId: 'peer-human',
      persona: 'Human-User',
      color: '#fafafa',
      participantType: 'human',
    });

    it('shows agents first when participants provided', async () => {
      const pMap = makeParticipantsMap([alpha, beta]);

      const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local', null, pMap));

      await act(async () => {
        result.current.handleChange('@', 1);
      });

      const items = result.current.items;
      // First items should be agents
      expect(items[0].kind).toBe('agent');
      if (items[0].kind === 'agent') {
        expect(items[0].participant.persona).toBe('Ravn-Alpha');
      }
      expect(items[1].kind).toBe('agent');
      // Then files follow
      const fileItems = items.filter(i => i.kind === 'file');
      expect(fileItems.length).toBe(3);
    });

    it('excludes human participants from agent items', async () => {
      const pMap = makeParticipantsMap([alpha, human]);

      const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local', null, pMap));

      await act(async () => {
        result.current.handleChange('@', 1);
      });

      const agentItems = result.current.items.filter(i => i.kind === 'agent');
      expect(agentItems).toHaveLength(1);
      if (agentItems[0].kind === 'agent') {
        expect(agentItems[0].participant.persona).toBe('Ravn-Alpha');
      }
    });

    it('fuzzy filters agent items by persona name', async () => {
      const pMap = makeParticipantsMap([alpha, beta]);

      const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local', null, pMap));

      await act(async () => {
        result.current.handleChange('@alpha', 6);
      });

      const agentItems = result.current.items.filter(i => i.kind === 'agent');
      expect(agentItems).toHaveLength(1);
      if (agentItems[0].kind === 'agent') {
        expect(agentItems[0].participant.persona).toBe('Ravn-Alpha');
      }
    });

    it('selectItem adds agent mention and returns persona name', async () => {
      const pMap = makeParticipantsMap([alpha]);

      const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local', null, pMap));

      await act(async () => {
        result.current.handleChange('@', 1);
      });

      const agentItem = result.current.items.find(i => i.kind === 'agent')!;
      let label = '';
      act(() => {
        label = result.current.selectItem(agentItem);
      });

      expect(label).toBe('Ravn-Alpha');
      expect(result.current.isOpen).toBe(false);
      expect(result.current.mentions).toHaveLength(1);
      const mention = result.current.mentions[0];
      expect(mention.kind).toBe('agent');
      if (mention.kind === 'agent') {
        expect(mention.participant.peerId).toBe('peer-alpha');
      }
    });

    it('selectItem does not add duplicate agent mentions', async () => {
      const pMap = makeParticipantsMap([alpha]);

      const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local', null, pMap));

      await act(async () => {
        result.current.handleChange('@', 1);
      });

      const agentItem = result.current.items.find(i => i.kind === 'agent')!;
      act(() => {
        result.current.selectItem(agentItem);
      });

      // Re-open
      mockFetchResponse(DEFAULT_FILES);
      await act(async () => {
        result.current.handleChange('@', 1);
      });

      const sameItem = result.current.items.find(i => i.kind === 'agent')!;
      act(() => {
        result.current.selectItem(sameItem);
      });

      expect(result.current.mentions).toHaveLength(1);
    });

    it('removeMention removes agent by peerId', async () => {
      const pMap = makeParticipantsMap([alpha]);

      const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local', null, pMap));

      await act(async () => {
        result.current.handleChange('@', 1);
      });

      const agentItem = result.current.items.find(i => i.kind === 'agent')!;
      act(() => {
        result.current.selectItem(agentItem);
      });

      expect(result.current.mentions).toHaveLength(1);

      act(() => {
        result.current.removeMention('peer-alpha');
      });

      expect(result.current.mentions).toHaveLength(0);
    });

    it('opens with agent-only items when no apiBase', async () => {
      const pMap = makeParticipantsMap([alpha]);

      const { result } = renderHook(() => useMentionMenu('session-1', null, null, pMap));

      await act(async () => {
        result.current.handleChange('@', 1);
      });

      expect(result.current.isOpen).toBe(true);
      expect(mockFetch).not.toHaveBeenCalled();
      const agentItems = result.current.items.filter(i => i.kind === 'agent');
      expect(agentItems).toHaveLength(1);
    });

    it('expandDirectory does nothing for agent items', async () => {
      const pMap = makeParticipantsMap([alpha]);

      const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local', null, pMap));

      await act(async () => {
        result.current.handleChange('@', 1);
      });

      const agentItem = result.current.items.find(i => i.kind === 'agent')!;
      const itemCountBefore = result.current.items.length;

      await act(async () => {
        result.current.expandDirectory(agentItem);
      });

      // No additional fetches for agent items
      expect(result.current.items.length).toBe(itemCountBefore);
    });

    it('Tab key does not expand agent items', async () => {
      mockFetch.mockReset();
      mockFetchResponse(DEFAULT_FILES);
      const pMap = makeParticipantsMap([alpha]);

      const { result } = renderHook(() => useMentionMenu('session-1', 'pod.local', null, pMap));

      await act(async () => {
        result.current.handleChange('@', 1);
      });

      // selectedIndex is 0 → agent item
      expect(result.current.items[0].kind).toBe('agent');

      // Tab should return true (handled) but not fetch children
      let handled = false;
      await act(async () => {
        handled = result.current.handleKeyDown({
          key: 'Tab',
          preventDefault: () => {},
        } as React.KeyboardEvent);
      });

      // Tab on agent item: not a directory, so no expansion fetch
      // Returns false (no special handling for agent tab)
      expect(handled).toBe(false);
      // No directory fetch beyond the initial file listing
      expect(mockFetch).toHaveBeenCalledTimes(1);
    });
  });
});
