import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useEffect } from 'react';
import {
  CommandPaletteProvider,
  useCommandPalette,
  useCommandPaletteRegistry,
  type Command,
} from './CommandPalette';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function setup() {
  return userEvent.setup();
}

/** Minimal component that registers commands and exposes open/close controls. */
function TestHarness({
  commands = [],
  initialOpen = false,
}: {
  commands?: Command[];
  initialOpen?: boolean;
}) {
  return (
    <CommandPaletteProvider initialOpen={initialOpen}>
      <TestInner commands={commands} />
    </CommandPaletteProvider>
  );
}

function TestInner({ commands }: { commands: Command[] }) {
  const { open, setOpen } = useCommandPalette();
  const { register, unregister } = useCommandPaletteRegistry();

  useEffect(() => {
    for (const cmd of commands) {
      register(cmd);
    }
    return () => {
      for (const cmd of commands) {
        unregister(cmd.id);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      <div data-testid="open-state">{open ? 'open' : 'closed'}</div>
      <button onClick={() => setOpen(true)} data-testid="btn-open">
        Open
      </button>
      <button onClick={() => setOpen(false)} data-testid="btn-close">
        Close
      </button>
    </>
  );
}

const sampleCommands: Command[] = [
  {
    id: 'cmd-alpha',
    title: 'Alpha',
    subtitle: 'first command',
    keywords: ['a', 'start'],
    execute: vi.fn(),
  },
  {
    id: 'cmd-beta',
    title: 'Beta',
    subtitle: 'second command',
    keywords: ['b'],
    execute: vi.fn(),
  },
  {
    id: 'cmd-gamma',
    title: 'Gamma',
    subtitle: 'third command',
    keywords: ['g'],
    execute: vi.fn(),
  },
];

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Provider & hooks
// ---------------------------------------------------------------------------

describe('CommandPaletteProvider', () => {
  it('renders children', () => {
    render(
      <CommandPaletteProvider>
        <span data-testid="child">hello</span>
      </CommandPaletteProvider>,
    );
    expect(screen.getByTestId('child')).toBeInTheDocument();
  });

  it('palette is closed by default', () => {
    render(<TestHarness />);
    expect(screen.getByTestId('open-state').textContent).toBe('closed');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('respects initialOpen=true', () => {
    render(<TestHarness initialOpen />);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});

describe('useCommandPalette', () => {
  it('throws when used outside provider', () => {
    // Suppress React error boundary output
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => {
      function Bad() {
        useCommandPalette();
        return null;
      }
      render(<Bad />);
    }).toThrow('useCommandPalette must be used within a <CommandPaletteProvider>');
    spy.mockRestore();
  });
});

describe('useCommandPaletteRegistry', () => {
  it('throws when used outside provider', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => {
      function Bad() {
        useCommandPaletteRegistry();
        return null;
      }
      render(<Bad />);
    }).toThrow('useCommandPaletteRegistry must be used within a <CommandPaletteProvider>');
    spy.mockRestore();
  });
});

// ---------------------------------------------------------------------------
// Open / close
// ---------------------------------------------------------------------------

describe('open / close', () => {
  it('opens via setOpen(true)', async () => {
    const user = setup();
    render(<TestHarness />);
    await user.click(screen.getByTestId('btn-open'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('closes via setOpen(false)', async () => {
    render(<TestHarness initialOpen />);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    // btn-close is behind the overlay — use fireEvent which bypasses pointer-events
    fireEvent.click(screen.getByTestId('btn-close'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('⌘K (metaKey) opens the palette', async () => {
    const user = setup();
    render(<TestHarness />);
    await user.keyboard('{Meta>}k{/Meta}');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('Ctrl+K opens the palette', async () => {
    const user = setup();
    render(<TestHarness />);
    await user.keyboard('{Control>}k{/Control}');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('⌘K toggles the palette closed', async () => {
    const user = setup();
    render(<TestHarness />);
    await user.keyboard('{Meta>}k{/Meta}');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.keyboard('{Meta>}k{/Meta}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('Escape closes the palette', async () => {
    const user = setup();
    render(<TestHarness />);
    await user.keyboard('{Control>}k{/Control}');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});

// ---------------------------------------------------------------------------
// Command registry
// ---------------------------------------------------------------------------

describe('command registry', () => {
  it('registered commands appear in the list when palette is open', async () => {
    render(<TestHarness commands={sampleCommands} initialOpen />);
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
    expect(screen.getByText('Gamma')).toBeInTheDocument();
  });

  it('subtitles are shown alongside titles', () => {
    render(<TestHarness commands={sampleCommands} initialOpen />);
    expect(screen.getByText('first command')).toBeInTheDocument();
  });

  it('shows empty state when no commands are registered', () => {
    render(<TestHarness commands={[]} initialOpen />);
    expect(screen.getByText('No commands found')).toBeInTheDocument();
  });

  it('unregistered commands disappear after unmount', async () => {
    function Togglable({ show }: { show: boolean }) {
      const { register, unregister } = useCommandPaletteRegistry();
      useEffect(() => {
        if (!show) return;
        register({ id: 'tmp', title: 'Temporary', execute: vi.fn() });
        return () => unregister('tmp');
      }, [show, register, unregister]);
      return null;
    }

    const { rerender } = render(
      <CommandPaletteProvider initialOpen>
        <Togglable show />
      </CommandPaletteProvider>,
    );

    expect(screen.getByText('Temporary')).toBeInTheDocument();

    rerender(
      <CommandPaletteProvider initialOpen>
        <Togglable show={false} />
      </CommandPaletteProvider>,
    );

    await waitFor(() => expect(screen.queryByText('Temporary')).toBeNull());
  });
});

// ---------------------------------------------------------------------------
// Fuzzy search / filtering
// ---------------------------------------------------------------------------

describe('fuzzy search', () => {
  it('shows all commands when query is empty', () => {
    render(<TestHarness commands={sampleCommands} initialOpen />);
    expect(screen.queryByText('No commands found')).toBeNull();
    expect(screen.getAllByRole('option')).toHaveLength(3);
  });

  it('filters by title substring', async () => {
    const user = setup();
    render(<TestHarness commands={sampleCommands} initialOpen />);
    await user.type(screen.getByRole('combobox'), 'alp');
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(screen.getByText('Alpha')).toBeInTheDocument();
  });

  it('filters by keyword', async () => {
    const user = setup();
    render(<TestHarness commands={sampleCommands} initialOpen />);
    await user.type(screen.getByRole('combobox'), 'start');
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(screen.getByText('Alpha')).toBeInTheDocument();
  });

  it('filters by subtitle', async () => {
    const user = setup();
    render(<TestHarness commands={sampleCommands} initialOpen />);
    await user.type(screen.getByRole('combobox'), 'third');
    expect(screen.getByText('Gamma')).toBeInTheDocument();
  });

  it('shows empty state when nothing matches', async () => {
    const user = setup();
    render(<TestHarness commands={sampleCommands} initialOpen />);
    await user.type(screen.getByRole('combobox'), 'zzznomatch');
    expect(screen.getByRole('status')).toHaveTextContent('No commands found');
  });

  it('is case-insensitive', async () => {
    const user = setup();
    render(<TestHarness commands={sampleCommands} initialOpen />);
    await user.type(screen.getByRole('combobox'), 'ALPHA');
    expect(screen.getByText('Alpha')).toBeInTheDocument();
  });

  it('fuzzy matches non-consecutive chars', async () => {
    const user = setup();
    render(<TestHarness commands={sampleCommands} initialOpen />);
    // 'Aa' should match 'Alpha' via fuzzy (A + a in sequence)
    await user.type(screen.getByRole('combobox'), 'Aa');
    expect(screen.getByText('Alpha')).toBeInTheDocument();
  });

  it('resets query on close and re-open', async () => {
    const user = setup();
    render(<TestHarness commands={sampleCommands} />);

    // Open and type
    await user.click(screen.getByTestId('btn-open'));
    await user.type(screen.getByRole('combobox'), 'alp');
    expect(screen.getAllByRole('option')).toHaveLength(1);

    // Close — btn-close is behind the overlay, use fireEvent
    fireEvent.click(screen.getByTestId('btn-close'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    // Re-open — all items should be back
    await user.click(screen.getByTestId('btn-open'));
    expect(screen.getAllByRole('option')).toHaveLength(3);
  });
});

// ---------------------------------------------------------------------------
// Keyboard navigation
// ---------------------------------------------------------------------------

describe('keyboard navigation', () => {
  it('first item is active by default', () => {
    render(<TestHarness commands={sampleCommands} initialOpen />);
    const options = screen.getAllByRole('option');
    expect(options[0]).toHaveAttribute('aria-selected', 'true');
    expect(options[1]).toHaveAttribute('aria-selected', 'false');
  });

  it('ArrowDown moves selection down', async () => {
    const user = setup();
    render(<TestHarness commands={sampleCommands} initialOpen />);
    await user.keyboard('{ArrowDown}');
    const options = screen.getAllByRole('option');
    expect(options[0]).toHaveAttribute('aria-selected', 'false');
    expect(options[1]).toHaveAttribute('aria-selected', 'true');
  });

  it('ArrowUp moves selection up, clamped at 0', async () => {
    const user = setup();
    render(<TestHarness commands={sampleCommands} initialOpen />);
    // Go down then back up
    await user.keyboard('{ArrowDown}');
    await user.keyboard('{ArrowUp}');
    const options = screen.getAllByRole('option');
    expect(options[0]).toHaveAttribute('aria-selected', 'true');
  });

  it('ArrowDown is clamped at last item', async () => {
    const user = setup();
    render(<TestHarness commands={sampleCommands} initialOpen />);
    await user.keyboard('{ArrowDown}');
    await user.keyboard('{ArrowDown}');
    await user.keyboard('{ArrowDown}'); // beyond end
    const options = screen.getAllByRole('option');
    expect(options[2]).toHaveAttribute('aria-selected', 'true');
  });

  it('Enter executes the active command and closes the palette', async () => {
    const executeFn = vi.fn();
    const cmds: Command[] = [{ id: 'exec', title: 'Execute Me', execute: executeFn }];
    const user = setup();
    render(<TestHarness commands={cmds} initialOpen />);
    await user.keyboard('{Enter}');
    expect(executeFn).toHaveBeenCalledOnce();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('Enter does nothing when no commands match', async () => {
    const user = setup();
    render(<TestHarness commands={sampleCommands} initialOpen />);
    await user.type(screen.getByRole('combobox'), 'zzznomatch');
    await user.keyboard('{Enter}');
    // Palette should still be open
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('hover on item updates active index', async () => {
    const user = setup();
    render(<TestHarness commands={sampleCommands} initialOpen />);
    const options = screen.getAllByRole('option');
    await user.hover(options[2]!);
    expect(options[2]).toHaveAttribute('aria-selected', 'true');
    expect(options[0]).toHaveAttribute('aria-selected', 'false');
  });
});

// ---------------------------------------------------------------------------
// Mouse click execution
// ---------------------------------------------------------------------------

describe('mouse click execution', () => {
  it('clicking a command executes it and closes the palette', async () => {
    const executeFn = vi.fn();
    const cmds: Command[] = [{ id: 'click-me', title: 'Click Me', execute: executeFn }];
    const user = setup();
    render(<TestHarness commands={cmds} initialOpen />);
    const [option] = screen.getAllByRole('option');
    await user.pointer({ keys: '[MouseLeft]', target: option! });
    expect(executeFn).toHaveBeenCalledOnce();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });
});

// ---------------------------------------------------------------------------
// Accessibility
// ---------------------------------------------------------------------------

describe('accessibility', () => {
  it('input has role combobox', () => {
    render(<TestHarness commands={sampleCommands} initialOpen />);
    expect(screen.getByRole('combobox')).toBeInTheDocument();
  });

  it('list has role listbox', () => {
    render(<TestHarness commands={sampleCommands} initialOpen />);
    expect(screen.getByRole('listbox')).toBeInTheDocument();
  });

  it('dialog has an accessible title', () => {
    render(<TestHarness initialOpen />);
    // Title exists in DOM (screen-reader only)
    expect(document.querySelector('.niuu-cp-sr-only')).toHaveTextContent('Command Palette');
  });

  it('input aria-activedescendant points to the active option', async () => {
    const user = setup();
    render(<TestHarness commands={sampleCommands} initialOpen />);
    await user.keyboard('{ArrowDown}');
    const input = screen.getByRole('combobox');
    const descendantId = input.getAttribute('aria-activedescendant');
    expect(descendantId).toBeTruthy();
    const target = document.getElementById(descendantId!);
    expect(target).toHaveTextContent('Beta');
  });
});
