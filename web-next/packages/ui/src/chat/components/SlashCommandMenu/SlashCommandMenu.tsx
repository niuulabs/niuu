import { useEffect, useRef } from 'react';
import { Slash, Hammer } from 'lucide-react';
import { cn } from '../../../utils/cn';
import type { SlashCommand } from '../../utils/slashCommands';
import './SlashCommandMenu.css';

interface SlashCommandMenuProps {
  id?: string;
  commands: SlashCommand[];
  selectedIndex: number;
  onSelect: (cmd: SlashCommand) => void;
}

export function SlashCommandMenu({ id, commands, selectedIndex, onSelect }: SlashCommandMenuProps) {
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
    <div className="niuu-chat-slash-menu" data-testid="slash-command-menu">
      <div className="niuu-chat-slash-heading">
        <strong>Slash commands</strong>
        <span>↑ ↓ browse · Enter to insert · Esc to close</span>
      </div>
      <div id={id} role="listbox" aria-label="Slash commands">
        {commands.map((cmd, i) => {
          const isSelected = i === selectedIndex;
          const Icon = cmd.type === 'skill' ? Hammer : Slash;
          return (
            <button
              key={cmd.name}
              id={id ? `${id}-${i}` : undefined}
              ref={isSelected ? selectedRef : undefined}
              type="button"
              className={cn('niuu-chat-slash-item', isSelected && 'niuu-chat-slash-item--selected')}
              role="option"
              aria-selected={isSelected}
              onClick={() => onSelect(cmd)}
            >
              <Icon className="niuu-chat-slash-icon" />
              <span className="niuu-chat-slash-copy">
                <span className="niuu-chat-slash-name">
                  /{cmd.name}
                  {cmd.argumentHint && (
                    <span className="niuu-chat-slash-hint"> {cmd.argumentHint}</span>
                  )}
                </span>
                {cmd.description ? (
                  <span className="niuu-chat-slash-description">{cmd.description}</span>
                ) : null}
              </span>
              <span className="niuu-chat-slash-type">{cmd.type}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
