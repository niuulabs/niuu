import { Eye, EyeOff } from 'lucide-react';
import { cn } from '@/utils';
import { FilterTabs } from '@/modules/shared/components/FilterTabs/FilterTabs';
import type { RoomParticipant } from '@/modules/shared/hooks/useSkuldChat';
import styles from './ParticipantFilter.module.css';

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

  function renderOption(peerId: string) {
    if (peerId === FILTER_ALL) return 'All';
    const p = participants.get(peerId);
    if (!p) return peerId;
    return (
      <>
        <span className={styles.dot} data-participant-color={p.color} />
        <span>{p.displayName ? `${p.displayName} (${p.persona})` : p.persona}</span>
      </>
    );
  }

  return (
    <div className={styles.container}>
      <FilterTabs
        options={options}
        value={activeFilter}
        onChange={onFilterChange}
        renderOption={renderOption}
      />

      <button
        type="button"
        className={cn(styles.toggleBtn, showInternal && styles.toggleActive)}
        onClick={onToggleInternal}
        title={showInternal ? 'Hide internal messages' : 'Show internal messages'}
        aria-pressed={showInternal}
      >
        {showInternal ? (
          <Eye className={styles.toggleIcon} />
        ) : (
          <EyeOff className={styles.toggleIcon} />
        )}
        <span className={styles.toggleLabel}>Internal</span>
      </button>
    </div>
  );
}
