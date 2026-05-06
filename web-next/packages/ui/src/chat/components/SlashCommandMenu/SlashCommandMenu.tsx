import { useEffect, useRef } from 'react';
import { Slash, Hammer } from 'lucide-react';
import { cn } from '../../../utils/cn';
import type { SlashCommand } from '../../utils/slashCommands';
import './SlashCommandMenu.css';

interface SlashCommandMenuProps {
  commands: SlashCommand[];
  selectedIndex: number;
  onSelect: (cmd: SlashCommand) => void;
}

export function SlashCommandMenu({ commands, selectedIndex, onSelect }: SlashCommandMenuProps) {
  const selectedRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    selectedRef.current?.scrollIntoView({ block: 'nearest' });
  }, [selectedIndex]);

  if (commands.length === 0) {
    return (
      <div className="niuu-chat-slash-menu" data-testid="slash-command-menu">
        <div className="niuu-chat-slash-empty">No matching commands</div>
      </div>
    );
  }

  return (
    <div className="niuu-chat-slash-menu" role="listbox" data-testid="slash-command-menu">
      {commands.map((cmd, i) => {
        const isSelected = i === selectedIndex;
        const Icon = cmd.type === 'skill' ? Hammer : Slash;
        return (
          <button
            key={cmd.name}
            ref={isSelected ? selectedRef : undefined}
            type="button"
            className={cn('niuu-chat-slash-item', isSelected && 'niuu-chat-slash-item--selected')}
            role="option"
            aria-selected={isSelected}
            onClick={() => onSelect(cmd)}
          >
            <Icon className="niuu-chat-slash-icon" />
            <span className="niuu-chat-slash-name">/{cmd.name}</span>
            <span className="niuu-chat-slash-type">{cmd.type}</span>
          </button>
        );
      })}
    </div>
  );
}
