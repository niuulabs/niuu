import { File, Folder, Loader2 } from 'lucide-react';
import { cn } from '../../../utils/cn';
import { resolveParticipantColor } from '../../utils/participantColor';
import type { MentionMenuItem } from '../../hooks/useMentionMenu';
import './MentionMenu.css';

interface MentionMenuProps {
  items: MentionMenuItem[];
  selectedIndex: number;
  loading: boolean;
  onSelect: (item: MentionMenuItem) => void;
  onExpand: (item: MentionMenuItem) => void;
}

export function MentionMenu({
  items,
  selectedIndex,
  loading,
  onSelect,
  onExpand,
}: MentionMenuProps) {
  const agentItems = items.filter((i) => i.kind === 'agent');
  const fileItems = items.filter((i) => i.kind === 'file');

  return (
    <div className="niuu-chat-mention-menu" role="listbox" data-testid="mention-menu">
      {agentItems.length > 0 && (
        <div className="niuu-chat-mention-section">
          <div className="niuu-chat-mention-section-header">Agents</div>
          {agentItems.map((item, _i) => {
            if (item.kind !== 'agent') return null;
            const { participant } = item;
            const color = resolveParticipantColor(participant.peerId, participant.color);
            const idx = items.indexOf(item);
            return (
              <button
                key={participant.peerId}
                type="button"
                className={cn(
                  'niuu-chat-mention-item',
                  idx === selectedIndex && 'niuu-chat-mention-item--selected',
                )}
                role="option"
                aria-selected={idx === selectedIndex}
                data-menu-index={idx}
                onClick={() => onSelect(item)}
              >
                <span
                  className="niuu-chat-mention-agent-dot"
                  style={{ backgroundColor: color }}
                  aria-hidden="true"
                />
                <span className="niuu-chat-mention-item-name">{participant.persona}</span>
              </button>
            );
          })}
        </div>
      )}
      {fileItems.length > 0 && (
        <div className="niuu-chat-mention-section">
          <div className="niuu-chat-mention-section-header">Files</div>
          {fileItems.map((item) => {
            if (item.kind !== 'file') return null;
            const { entry } = item;
            const idx = items.indexOf(item);
            const isDir = entry.type === 'directory';
            const Icon = isDir ? Folder : File;
            const depth = entry.depth ?? 0;
            return (
              <button
                key={entry.path}
                type="button"
                className={cn(
                  'niuu-chat-mention-item',
                  idx === selectedIndex && 'niuu-chat-mention-item--selected',
                )}
                role="option"
                aria-selected={idx === selectedIndex}
                data-menu-index={idx}
                style={{ paddingLeft: `${(depth + 1) * 12}px` }}
                onClick={() => (isDir ? onExpand(item) : onSelect(item))}
                onDoubleClick={() => isDir && onSelect(item)}
              >
                <Icon
                  className={cn(
                    'niuu-chat-mention-file-icon',
                    isDir && 'niuu-chat-mention-file-icon--dir',
                  )}
                />
                <span className="niuu-chat-mention-item-name">{entry.name}</span>
                {isDir && <span className="niuu-chat-mention-dir-badge">dir</span>}
              </button>
            );
          })}
        </div>
      )}
      {loading && (
        <div className="niuu-chat-mention-loading">
          <Loader2 className="niuu-chat-mention-spinner" />
        </div>
      )}
      {!loading && items.length === 0 && <div className="niuu-chat-mention-empty">No results</div>}
    </div>
  );
}
