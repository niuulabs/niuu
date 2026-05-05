import { useCallback, useState } from 'react';
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

export function useSlashMenu(availableCommands?: SlashCommand[]): UseSlashMenuReturn {
  const [isOpen, setIsOpen] = useState(false);
  const [filteredCommands, setFilteredCommands] = useState<SlashCommand[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);

  const selectCommand = useCallback((cmd: SlashCommand): string => {
    setIsOpen(false);
    return `/${cmd.name} `;
  }, []);

  const handleChange = useCallback(
    (value: string) => {
      if (!value.startsWith('/') || !availableCommands || availableCommands.length === 0) {
        setIsOpen(false);
        return;
      }
      const query = value.slice(1).toLowerCase();
      const filtered = availableCommands.filter((cmd) => cmd.name.toLowerCase().includes(query));
      setFilteredCommands(filtered);
      setSelectedIndex(0);
      setIsOpen(filtered.length > 0);
    },
    [availableCommands],
  );

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
