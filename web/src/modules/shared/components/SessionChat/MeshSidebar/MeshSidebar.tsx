import { useState } from 'react';
import type { RoomParticipant } from '@/modules/shared/hooks/useSkuldChat';
import styles from './MeshSidebar.module.css';

interface MeshSidebarProps {
  participants: ReadonlyMap<string, RoomParticipant>;
  selectedPeerId: string | null;
  onSelectPeer: (peerId: string) => void;
}

function PeerCard({
  participant,
  isSelected,
  onSelect,
}: {
  participant: RoomParticipant;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  const hasMetadata =
    (participant.subscribesTo && participant.subscribesTo.length > 0) ||
    (participant.emits && participant.emits.length > 0) ||
    (participant.tools && participant.tools.length > 0);

  return (
    <div
      className={styles.peerCard}
      data-selected={isSelected}
      data-participant-color={participant.color}
      onClick={onSelect}
    >
      <div className={styles.peerHeader}>
        <span className={styles.statusDot} data-status={participant.status} />
        <span className={styles.peerName}>
          {participant.displayName
            ? `${participant.displayName} (${participant.persona})`
            : participant.persona || participant.peerId}
        </span>
        <span className={styles.peerStatus}>{participant.status}</span>
      </div>

      {hasMetadata && (
        <button
          className={styles.expandToggle}
          onClick={e => {
            e.stopPropagation();
            setExpanded(!expanded);
          }}
        >
          {expanded ? 'hide details' : 'show details'}
        </button>
      )}

      {expanded && hasMetadata && (
        <div className={styles.metaSection}>
          {participant.subscribesTo && participant.subscribesTo.length > 0 && (
            <>
              <span className={styles.metaLabel}>Subscribes</span>
              <div className={styles.metaTags}>
                {participant.subscribesTo.map(evt => (
                  <span key={evt} className={styles.metaTag} data-variant="subscribe">
                    {evt}
                  </span>
                ))}
              </div>
            </>
          )}

          {participant.emits && participant.emits.length > 0 && (
            <>
              <span className={styles.metaLabel}>Emits</span>
              <div className={styles.metaTags}>
                {participant.emits.map(evt => (
                  <span key={evt} className={styles.metaTag} data-variant="emit">
                    {evt}
                  </span>
                ))}
              </div>
            </>
          )}

          {participant.tools && participant.tools.length > 0 && (
            <>
              <span className={styles.metaLabel}>Tools</span>
              <div className={styles.metaTags}>
                {participant.tools.map(tool => (
                  <span key={tool} className={styles.metaTag} data-variant="tool">
                    {tool}
                  </span>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function MeshSidebar({ participants, selectedPeerId, onSelectPeer }: MeshSidebarProps) {
  const peers = Array.from(participants.values()).filter(p => p.participantType === 'ravn');

  if (peers.length === 0) {
    return null;
  }

  return (
    <aside className={styles.sidebar}>
      <div className={styles.header}>
        <span className={styles.headerTitle}>Mesh Peers</span>
        <span className={styles.headerCount}>{peers.length}</span>
      </div>
      <div className={styles.peerList}>
        {peers.map(peer => (
          <PeerCard
            key={peer.peerId}
            participant={peer}
            isSelected={selectedPeerId === peer.peerId}
            onSelect={() => onSelectPeer(peer.peerId)}
          />
        ))}
      </div>
    </aside>
  );
}
