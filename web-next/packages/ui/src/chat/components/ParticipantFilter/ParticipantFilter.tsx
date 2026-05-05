import { Eye, EyeOff } from 'lucide-react';
import { cn } from '../../../utils/cn';
import type { RoomParticipant } from '../../types';
import './ParticipantFilter.css';

interface ParticipantFilterProps {
  participants: ReadonlyMap<string, RoomParticipant>;
  activeFilter: string;
  onFilterChange: (peerId: string) => void;
  showInternal: boolean;
  onToggleInternal: () => void;
}

const FILTER_ALL = 'all';

export function ParticipantFilter({
  participants,
  activeFilter,
  onFilterChange,
  showInternal,
  onToggleInternal,
}: ParticipantFilterProps) {
  const options = [FILTER_ALL, ...Array.from(participants.keys())];

  return (
    <div className="niuu-chat-participant-filter" data-testid="participant-filter">
      <div className="niuu-chat-filter-tabs">
        {options.map((peerId) => {
          const p = peerId === FILTER_ALL ? null : participants.get(peerId);
          const isActive = activeFilter === peerId;
          return (
            <button
              key={peerId}
              type="button"
              className={cn('niuu-chat-filter-tab', isActive && 'niuu-chat-filter-tab--active')}
              onClick={() => onFilterChange(peerId)}
              aria-pressed={isActive}
              data-testid={`filter-tab-${peerId}`}
            >
              {p && (
                <span
                  className="niuu-chat-filter-dot"
                  style={{ '--niuu-dot-color': p.color } as React.CSSProperties}
                />
              )}
              <span>
                {peerId === FILTER_ALL
                  ? 'All'
                  : p
                    ? p.displayName
                      ? `${p.displayName} (${p.persona})`
                      : p.persona
                    : peerId}
              </span>
            </button>
          );
        })}
      </div>

      <button
        type="button"
        className={cn(
          'niuu-chat-internal-toggle',
          showInternal && 'niuu-chat-internal-toggle--active',
        )}
        onClick={onToggleInternal}
        title={showInternal ? 'Hide internal messages' : 'Show internal messages'}
        aria-pressed={showInternal}
        data-testid="internal-toggle"
      >
        {showInternal ? (
          <Eye className="niuu-chat-toggle-icon" />
        ) : (
          <EyeOff className="niuu-chat-toggle-icon" />
        )}
        <span className="niuu-chat-toggle-label">Internal</span>
      </button>
    </div>
  );
}
