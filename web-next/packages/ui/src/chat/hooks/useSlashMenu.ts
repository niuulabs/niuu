import { useCallback, useMemo, useState } from 'react';
import type { KeyboardEvent } from 'react';
import type { SlashCommand } from '../utils/slashCommands';

interface UseSlashMenuReturn {
  isOpen: boolean;
  filteredCommands: SlashCommand[];
  selectedIndex: number;
  handleChange: (value: string) => void;
  handleKeyDown: (e: KeyboardEvent) => boolean;
  selectCommand: (cmd: SlashCommand) => string;
  close: () => void;
}

export function useSlashMenu(availableCommands?: readonly SlashCommand[]): UseSlashMenuReturn {
  const [wantsOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState<string | null>(null);
  const [selection, setSelectedIndex] = useState(0);
  const filteredCommands = useMemo(() => {
    if (query === null) return [];
    return (availableCommands ?? [])
      .filter((cmd) => cmd.name.toLowerCase().includes(query))
      .sort((a, b) => {
        const aPrefix = a.name.toLowerCase().startsWith(query);
        const bPrefix = b.name.toLowerCase().startsWith(query);
        return Number(bPrefix) - Number(aPrefix) || a.name.localeCompare(b.name);
      });
  }, [availableCommands, query]);
  const isOpen = wantsOpen && filteredCommands.length > 0;
  const selectedIndex = Math.min(selection, Math.max(0, filteredCommands.length - 1));

  const selectCommand = useCallback((cmd: SlashCommand): string => {
    setIsOpen(false);
    return `/${cmd.name} `;
  }, []);

  const handleChange = useCallback((value: string) => {
    const query = /^\/\S*$/.test(value) ? value.slice(1).toLowerCase() : null;
    setQuery(query);
    setSelectedIndex(0);
    setIsOpen(query !== null);
  }, []);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent): boolean => {
      if (!isOpen) return false;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % filteredCommands.length);
        return true;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex((prev) => (prev - 1 + filteredCommands.length) % filteredCommands.length);
        return true;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setIsOpen(false);
        return true;
      }
      if (e.key === 'Tab' || e.key === 'Enter') {
        const selected = filteredCommands[selectedIndex];
        if (!selected) return false;
        e.preventDefault();
        selectCommand(selected);
        return true;
      }
      return false;
    },
    [isOpen, filteredCommands, selectedIndex, selectCommand],
  );

  const close = useCallback(() => setIsOpen(false), []);

  return {
    isOpen,
    filteredCommands,
    selectedIndex,
    handleChange,
    handleKeyDown,
    selectCommand,
    close,
  };
}
