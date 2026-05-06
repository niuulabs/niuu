import * as RadixDialog from '@radix-ui/react-dialog';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { cn } from '../../utils/cn';
import './CommandPalette.css';

export interface Command {
  id: string;
  title: string;
  subtitle?: string;
  keywords?: string[];
  execute: () => void;
}

interface CommandPaletteContextValue {
  open: boolean;
  setOpen: (open: boolean) => void;
  register: (command: Command) => void;
  unregister: (id: string) => void;
}

const CommandPaletteContext = createContext<CommandPaletteContextValue | null>(null);

export interface CommandPaletteProviderProps {
  children: ReactNode;
  /** @internal For Storybook and tests — open the palette immediately. */
  initialOpen?: boolean;
}

export function CommandPaletteProvider({
  children,
  initialOpen = false,
}: CommandPaletteProviderProps) {
  const [open, setOpen] = useState(initialOpen);
  const [commands, setCommands] = useState<Map<string, Command>>(new Map());

  const register = useCallback((command: Command) => {
    setCommands((prev) => new Map(prev).set(command.id, command));
  }, []);

  const unregister = useCallback((id: string) => {
    setCommands((prev) => {
      const next = new Map(prev);
      next.delete(id);
      return next;
    });
  }, []);

  // ⌘K / Ctrl+K — toggle the palette from anywhere in the app
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  return (
    <CommandPaletteContext.Provider value={{ open, setOpen, register, unregister }}>
      {children}
      <CommandPaletteDialog open={open} onOpenChange={setOpen} commands={[...commands.values()]} />
    </CommandPaletteContext.Provider>
  );
}

export function useCommandPalette(): Pick<CommandPaletteContextValue, 'open' | 'setOpen'> {
  const ctx = useContext(CommandPaletteContext);
  if (ctx === null) {
    throw new Error('useCommandPalette must be used within a <CommandPaletteProvider>');
  }
  return { open: ctx.open, setOpen: ctx.setOpen };
}

export function useCommandPaletteRegistry(): Pick<
  CommandPaletteContextValue,
  'register' | 'unregister'
> {
  const ctx = useContext(CommandPaletteContext);
  if (ctx === null) {
    throw new Error('useCommandPaletteRegistry must be used within a <CommandPaletteProvider>');
  }
  return { register: ctx.register, unregister: ctx.unregister };
}

// ---------------------------------------------------------------------------
// Fuzzy search — all query chars must appear in order within the target
// ---------------------------------------------------------------------------

function fuzzyMatch(query: string, command: Command): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  const haystack = [command.title, command.subtitle ?? '', ...(command.keywords ?? [])]
    .join(' ')
    .toLowerCase();
  let h = 0;
  for (let qi = 0; qi < q.length; qi++) {
    h = haystack.indexOf(q[qi]!, h);
    if (h === -1) return false;
    h++;
  }
  return true;
}

// ---------------------------------------------------------------------------
// Dialog UI (internal)
// ---------------------------------------------------------------------------

interface CommandPaletteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  commands: Command[];
}

function CommandPaletteDialog({ open, onOpenChange, commands }: CommandPaletteDialogProps) {
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const listId = useId();

  const filtered = commands.filter((c) => fuzzyMatch(query, c));

  // Reset query + selection each time the palette opens
  useEffect(() => {
    if (!open) return;
    setQuery('');
    setActiveIndex(0);
  }, [open]);

  // Clamp active index when the filtered list shrinks
  useEffect(() => {
    setActiveIndex((i) => (filtered.length === 0 ? 0 : Math.min(i, filtered.length - 1)));
  }, [filtered.length]);

  // Keep the active item visible during keyboard navigation
  // scrollIntoView is not available in jsdom so guard the call
  useEffect(() => {
    const item = listRef.current?.children[activeIndex] as HTMLElement | undefined;
    item?.scrollIntoView?.({ block: 'nearest' });
  }, [activeIndex]);

  const handleInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, filtered.length - 1));
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      const cmd = filtered[activeIndex];
      if (cmd) {
        cmd.execute();
        onOpenChange(false);
      }
    }
  };

  const handleSelect = (cmd: Command) => {
    cmd.execute();
    onOpenChange(false);
  };

  const activeItemId =
    filtered[activeIndex] != null ? `${listId}-item-${filtered[activeIndex]!.id}` : undefined;

  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="niuu-cp-overlay" />
        <RadixDialog.Content
          className="niuu-cp-content"
          aria-describedby={undefined}
          onOpenAutoFocus={(e) => {
            // Prevent Radix from focusing the content — we focus the input
            e.preventDefault();
            inputRef.current?.focus();
          }}
        >
          <RadixDialog.Title className="niuu-cp-sr-only">Command Palette</RadixDialog.Title>

          <div className="niuu-cp-search">
            <span className="niuu-cp-icon" aria-hidden="true">
              ⌘
            </span>
            <input
              ref={inputRef}
              role="combobox"
              aria-autocomplete="list"
              aria-haspopup="listbox"
              aria-expanded={filtered.length > 0}
              aria-controls={listId}
              aria-activedescendant={activeItemId}
              className="niuu-cp-input"
              placeholder="Search commands…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setActiveIndex(0);
              }}
              onKeyDown={handleInputKeyDown}
            />
          </div>

          {filtered.length === 0 ? (
            <div className="niuu-cp-empty" role="status">
              No commands found
            </div>
          ) : (
            <ul
              ref={listRef}
              id={listId}
              role="listbox"
              aria-label="Commands"
              className="niuu-cp-list"
            >
              {filtered.map((cmd, i) => (
                <li
                  key={cmd.id}
                  id={`${listId}-item-${cmd.id}`}
                  role="option"
                  aria-selected={i === activeIndex}
                  className={cn('niuu-cp-item', i === activeIndex && 'niuu-cp-item--active')}
                  onPointerDown={(e) => {
                    // Prevent the input from losing focus
                    e.preventDefault();
                    handleSelect(cmd);
                  }}
                  onMouseEnter={() => setActiveIndex(i)}
                >
                  <span className="niuu-cp-item-title">{cmd.title}</span>
                  {cmd.subtitle != null && (
                    <span className="niuu-cp-item-subtitle">{cmd.subtitle}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
