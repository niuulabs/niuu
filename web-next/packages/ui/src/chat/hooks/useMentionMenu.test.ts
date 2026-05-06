import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useMentionMenu } from './useMentionMenu';
import type { RoomParticipant, FileEntry } from '../types';

const p1: RoomParticipant = {
  peerId: 'p1',
  persona: 'Ada',
  color: '#38bdf8',
  participantType: 'ravn',
};
const p2: RoomParticipant = {
  peerId: 'p2',
  persona: 'Björk',
  color: '#a78bfa',
  participantType: 'human',
};
const participants = new Map([
  ['p1', p1],
  ['p2', p2],
]);

const fileEntry: FileEntry = { name: 'foo.ts', path: '/src/foo.ts', type: 'file' };
const dirEntry: FileEntry = { name: 'src', path: '/src', type: 'directory' };

const makeKeyEvent = (key: string) =>
  ({ key, preventDefault: () => {} }) as unknown as import('react').KeyboardEvent;

describe('useMentionMenu — basic state', () => {
  it('starts closed', () => {
    const { result } = renderHook(() => useMentionMenu());
    expect(result.current.isOpen).toBe(false);
    expect(result.current.items).toHaveLength(0);
    expect(result.current.mentions).toHaveLength(0);
  });

  it('does not open if no @ in text', () => {
    const { result } = renderHook(() => useMentionMenu(null, null, null, participants));
    act(() => {
      result.current.handleChange('hello', 5);
    });
    expect(result.current.isOpen).toBe(false);
  });

  it('opens when @ found in text', () => {
    const { result } = renderHook(() => useMentionMenu(null, null, null, participants));
    act(() => {
      result.current.handleChange('@', 1);
    });
    expect(result.current.isOpen).toBe(true);
    expect(result.current.items).toHaveLength(1);
  });

  it('closes when space follows @-query', () => {
    const { result } = renderHook(() => useMentionMenu(null, null, null, participants));
    act(() => {
      result.current.handleChange('@Ada hey', 8);
    });
    expect(result.current.isOpen).toBe(false);
  });

  it('filters participants by query', () => {
    const { result } = renderHook(() => useMentionMenu(null, null, null, participants));
    act(() => {
      result.current.handleChange('@ada', 4);
    });
    expect(result.current.items).toHaveLength(1);
    expect(
      (result.current.items[0] as { kind: 'agent'; participant: RoomParticipant }).participant
        .persona,
    ).toBe('Ada');
  });

  it('only offers ravn participants for agent mentions', () => {
    const { result } = renderHook(() => useMentionMenu(null, null, null, participants));
    act(() => {
      result.current.handleChange('@', 1);
    });
    expect(result.current.items).toHaveLength(1);
    expect(
      (result.current.items[0] as { kind: 'agent'; participant: RoomParticipant }).participant
        .persona,
    ).toBe('Ada');
  });
});

describe('useMentionMenu — keyboard navigation', () => {
  it('ArrowDown moves selectedIndex forward', () => {
    const { result } = renderHook(() => useMentionMenu(null, null, null, participants));
    act(() => {
      result.current.handleChange('@', 1);
    });
    act(() => {
      result.current.handleKeyDown(makeKeyEvent('ArrowDown'));
    });
    expect(result.current.selectedIndex).toBe(0);
  });

  it('ArrowUp wraps to last item', () => {
    const { result } = renderHook(() => useMentionMenu(null, null, null, participants));
    act(() => {
      result.current.handleChange('@', 1);
    });
    act(() => {
      result.current.handleKeyDown(makeKeyEvent('ArrowUp'));
    });
    expect(result.current.selectedIndex).toBe(0);
  });

  it('Escape closes menu', () => {
    const { result } = renderHook(() => useMentionMenu(null, null, null, participants));
    act(() => {
      result.current.handleChange('@', 1);
    });
    act(() => {
      result.current.handleKeyDown(makeKeyEvent('Escape'));
    });
    expect(result.current.isOpen).toBe(false);
  });

  it('Enter returns true when item selected', () => {
    const { result } = renderHook(() => useMentionMenu(null, null, null, participants));
    act(() => {
      result.current.handleChange('@', 1);
    });
    let handled = false;
    act(() => {
      handled = result.current.handleKeyDown(makeKeyEvent('Enter'));
    });
    expect(handled).toBe(true);
  });

  it('Tab returns true when item selected', () => {
    const { result } = renderHook(() => useMentionMenu(null, null, null, participants));
    act(() => {
      result.current.handleChange('@', 1);
    });
    let handled = false;
    act(() => {
      handled = result.current.handleKeyDown(makeKeyEvent('Tab'));
    });
    expect(handled).toBe(true);
  });

  it('returns false when menu closed', () => {
    const { result } = renderHook(() => useMentionMenu());
    let handled = true;
    act(() => {
      handled = result.current.handleKeyDown(makeKeyEvent('ArrowDown'));
    });
    expect(handled).toBe(false);
  });

  it('Enter returns false when no items', () => {
    const { result } = renderHook(() => useMentionMenu());
    // No items, force isOpen somehow — it stays closed, so Enter returns false
    let handled = true;
    act(() => {
      handled = result.current.handleKeyDown(makeKeyEvent('Enter'));
    });
    expect(handled).toBe(false);
  });
});

describe('useMentionMenu — selectItem', () => {
  it('selecting an agent adds it to mentions', () => {
    const { result } = renderHook(() => useMentionMenu(null, null, null, participants));
    act(() => {
      result.current.handleChange('@', 1);
    });
    const agentItem = result.current.items.find((i) => i.kind === 'agent') as {
      kind: 'agent';
      participant: RoomParticipant;
    };
    act(() => {
      result.current.selectItem(agentItem!);
    });
    expect(result.current.mentions.some((m) => m.kind === 'agent')).toBe(true);
    expect(result.current.isOpen).toBe(false);
  });

  it('selecting same agent twice does not duplicate', () => {
    const { result } = renderHook(() => useMentionMenu(null, null, null, participants));
    act(() => {
      result.current.handleChange('@', 1);
    });
    const agentItem = result.current.items[0] as { kind: 'agent'; participant: RoomParticipant };
    act(() => {
      result.current.selectItem(agentItem);
    });
    act(() => {
      result.current.handleChange('@', 1);
    });
    act(() => {
      result.current.selectItem(agentItem);
    });
    expect(
      result.current.mentions.filter(
        (m) => m.kind === 'agent' && m.participant.peerId === agentItem.participant.peerId,
      ),
    ).toHaveLength(1);
  });

  it('selecting a file item adds it to mentions', () => {
    const { result } = renderHook(() => useMentionMenu());
    const fileItem = { kind: 'file' as const, entry: fileEntry };
    act(() => {
      result.current.selectItem(fileItem);
    });
    expect(result.current.mentions.some((m) => m.kind === 'file')).toBe(true);
  });

  it('selecting same file twice does not duplicate', () => {
    const { result } = renderHook(() => useMentionMenu());
    const fileItem = { kind: 'file' as const, entry: fileEntry };
    act(() => {
      result.current.selectItem(fileItem);
    });
    act(() => {
      result.current.selectItem(fileItem);
    });
    expect(result.current.mentions.filter((m) => m.kind === 'file')).toHaveLength(1);
  });

  it('removeMention removes the mention by id', () => {
    const { result } = renderHook(() => useMentionMenu(null, null, null, participants));
    act(() => {
      result.current.handleChange('@', 1);
    });
    const agentItem = result.current.items[0] as { kind: 'agent'; participant: RoomParticipant };
    act(() => {
      result.current.selectItem(agentItem);
    });
    act(() => {
      result.current.removeMention(agentItem.participant.peerId);
    });
    expect(result.current.mentions).toHaveLength(0);
  });

  it('removeMention removes file mention by path', () => {
    const { result } = renderHook(() => useMentionMenu());
    const fileItem = { kind: 'file' as const, entry: fileEntry };
    act(() => {
      result.current.selectItem(fileItem);
    });
    act(() => {
      result.current.removeMention(fileEntry.path);
    });
    expect(result.current.mentions).toHaveLength(0);
  });
});

describe('useMentionMenu — close', () => {
  it('close() closes the menu', () => {
    const { result } = renderHook(() => useMentionMenu(null, null, null, participants));
    act(() => {
      result.current.handleChange('@', 1);
    });
    act(() => {
      result.current.close();
    });
    expect(result.current.isOpen).toBe(false);
  });
});

describe('useMentionMenu — expandDirectory', () => {
  it('ignores non-directory items', () => {
    const { result } = renderHook(() => useMentionMenu());
    const fileItem = { kind: 'file' as const, entry: fileEntry };
    // Should not throw
    act(() => {
      result.current.expandDirectory(fileItem);
    });
  });

  it('ignores agent items', () => {
    const { result } = renderHook(() => useMentionMenu(null, null, null, participants));
    const agentItem = { kind: 'agent' as const, participant: p1 };
    act(() => {
      result.current.expandDirectory(agentItem);
    });
  });

  it('calls onFetchFiles when expanding a directory', async () => {
    const onFetchFiles = vi.fn().mockResolvedValue([fileEntry]);
    const { result } = renderHook(() =>
      useMentionMenu('sid', 'host:8080', null, new Map(), onFetchFiles),
    );
    const dirItem = { kind: 'file' as const, entry: dirEntry };
    await act(async () => {
      result.current.expandDirectory(dirItem);
    });
    expect(onFetchFiles).toHaveBeenCalledWith('/src', 'http://host:8080');
  });
});

describe('useMentionMenu — file fetching', () => {
  it('fetches files when onFetchFiles provided with sessionHost', async () => {
    const onFetchFiles = vi.fn().mockResolvedValue([fileEntry]);
    const { result } = renderHook(() =>
      useMentionMenu('sid', 'host:8080', null, participants, onFetchFiles),
    );
    await act(async () => {
      result.current.handleChange('@', 1);
      await Promise.resolve();
    });
    expect(onFetchFiles).toHaveBeenCalled();
  });

  it('fetches files using chatEndpoint when provided', async () => {
    const onFetchFiles = vi.fn().mockResolvedValue([fileEntry]);
    const { result } = renderHook(() =>
      useMentionMenu('sid', null, 'http://myhost/api/chat', participants, onFetchFiles),
    );
    await act(async () => {
      result.current.handleChange('@', 1);
      await Promise.resolve();
    });
    expect(onFetchFiles).toHaveBeenCalledWith('', 'http://myhost/api');
  });

  it('gracefully handles fetch error', async () => {
    const onFetchFiles = vi.fn().mockRejectedValue(new Error('network error'));
    const { result } = renderHook(() =>
      useMentionMenu('sid', 'host:8080', null, participants, onFetchFiles),
    );
    await act(async () => {
      result.current.handleChange('@', 1);
      await Promise.resolve();
    });
    // Should not throw — loading should be false after error
    expect(result.current.loading).toBe(false);
  });
});
