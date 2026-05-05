import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useSlashMenu } from './useSlashMenu';
import type { SlashCommand } from '../utils/slashCommands';

const commands: SlashCommand[] = [
  { name: 'clear', type: 'command' },
  { name: 'compact', type: 'command' },
  { name: 'summarize', type: 'skill' },
];

const makeKeyEvent = (key: string, extra?: Partial<KeyboardEvent>) =>
  ({ key, preventDefault: () => {}, ...extra }) as unknown as import('react').KeyboardEvent;

describe('useSlashMenu', () => {
  it('starts closed with empty filtered commands', () => {
    const { result } = renderHook(() => useSlashMenu(commands));
    expect(result.current.isOpen).toBe(false);
    expect(result.current.filteredCommands).toHaveLength(0);
  });

  it('opens menu when input starts with /', () => {
    const { result } = renderHook(() => useSlashMenu(commands));
    act(() => {
      result.current.handleChange('/');
    });
    expect(result.current.isOpen).toBe(true);
    expect(result.current.filteredCommands).toHaveLength(3);
  });

  it('filters commands by query', () => {
    const { result } = renderHook(() => useSlashMenu(commands));
    act(() => {
      result.current.handleChange('/cl');
    });
    expect(result.current.filteredCommands.map((c) => c.name)).toEqual(['clear']);
  });

  it('closes when input does not start with /', () => {
    const { result } = renderHook(() => useSlashMenu(commands));
    act(() => {
      result.current.handleChange('/');
    });
    act(() => {
      result.current.handleChange('hello');
    });
    expect(result.current.isOpen).toBe(false);
  });

  it('closes when no commands match the query', () => {
    const { result } = renderHook(() => useSlashMenu(commands));
    act(() => {
      result.current.handleChange('/xyz');
    });
    expect(result.current.isOpen).toBe(false);
  });

  it('does not open when no availableCommands', () => {
    const { result } = renderHook(() => useSlashMenu(undefined));
    act(() => {
      result.current.handleChange('/');
    });
    expect(result.current.isOpen).toBe(false);
  });

  it('ArrowDown navigates forward', () => {
    const { result } = renderHook(() => useSlashMenu(commands));
    act(() => {
      result.current.handleChange('/');
    });
    act(() => {
      result.current.handleKeyDown(makeKeyEvent('ArrowDown'));
    });
    expect(result.current.selectedIndex).toBe(1);
  });

  it('ArrowUp navigates backward and wraps', () => {
    const { result } = renderHook(() => useSlashMenu(commands));
    act(() => {
      result.current.handleChange('/');
    });
    act(() => {
      result.current.handleKeyDown(makeKeyEvent('ArrowUp'));
    });
    expect(result.current.selectedIndex).toBe(commands.length - 1);
  });

  it('Escape closes the menu', () => {
    const { result } = renderHook(() => useSlashMenu(commands));
    act(() => {
      result.current.handleChange('/');
    });
    act(() => {
      result.current.handleKeyDown(makeKeyEvent('Escape'));
    });
    expect(result.current.isOpen).toBe(false);
  });

  it('Enter selects current item and closes menu', () => {
    const { result } = renderHook(() => useSlashMenu(commands));
    act(() => {
      result.current.handleChange('/');
    });
    act(() => {
      result.current.handleKeyDown(makeKeyEvent('Enter'));
    });
    expect(result.current.isOpen).toBe(false);
  });

  it('Tab returns true when item selected', () => {
    const { result } = renderHook(() => useSlashMenu(commands));
    act(() => {
      result.current.handleChange('/');
    });
    let handled: boolean = false;
    act(() => {
      handled = result.current.handleKeyDown(makeKeyEvent('Tab'));
    });
    expect(handled).toBe(true);
  });

  it('handleKeyDown returns false when menu closed', () => {
    const { result } = renderHook(() => useSlashMenu(commands));
    let handled: boolean = true;
    act(() => {
      handled = result.current.handleKeyDown(makeKeyEvent('ArrowDown'));
    });
    expect(handled).toBe(false);
  });

  it('selectCommand closes menu and returns slash-prefixed text', () => {
    const { result } = renderHook(() => useSlashMenu(commands));
    act(() => {
      result.current.handleChange('/');
    });
    let text = '';
    act(() => {
      text = result.current.selectCommand(commands[0]);
    });
    expect(text).toBe('/clear ');
    expect(result.current.isOpen).toBe(false);
  });

  it('close() closes the menu', () => {
    const { result } = renderHook(() => useSlashMenu(commands));
    act(() => {
      result.current.handleChange('/');
    });
    act(() => {
      result.current.close();
    });
    expect(result.current.isOpen).toBe(false);
  });

  it('Enter returns false when no selected item', () => {
    const { result } = renderHook(() => useSlashMenu([]));
    // force open state by passing empty commands — stays closed actually
    let handled: boolean = true;
    act(() => {
      handled = result.current.handleKeyDown(makeKeyEvent('Enter'));
    });
    expect(handled).toBe(false);
  });
});
