import { useEffect, useRef } from 'react';
import { File, Folder, FolderOpen, Loader2 } from 'lucide-react';
import type { MentionItem } from '../useMentionMenu';
import styles from './MentionMenu.module.css';

interface MentionMenuProps {
  items: MentionItem[];
  selectedIndex: number;
  loading: boolean;
  onSelect: (item: MentionItem) => void;
  onExpand: (item: MentionItem) => void;
}

export function MentionMenu({
  items,
  selectedIndex,
  loading,
  onSelect,
  onExpand,
}: MentionMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);

  // Auto-scroll selected item into view using data-menu-index attribute
  useEffect(() => {
    const menu = menuRef.current;
    if (!menu) {
      return;
    }
    const selected = menu.querySelector(
      `[data-menu-index="${selectedIndex}"]`
    ) as HTMLElement | null;
    selected?.scrollIntoView({ block: 'nearest' });
  }, [selectedIndex]);

  const agentItems = items.filter(item => item.kind === 'agent');
  const fileItems = items.filter(item => item.kind === 'file');
  const hasAgents = agentItems.length > 0;
  const hasFiles = fileItems.length > 0;

  // Global index counter across both sections for selectedIndex matching
  let globalIndex = 0;

  if (loading && items.length === 0) {
    return (
      <div className={styles.menu} data-testid="mention-menu">
        <div className={styles.loading}>
          <Loader2 className={styles.spinner} />
          <span>Loading files...</span>
        </div>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className={styles.menu} data-testid="mention-menu">
        <div className={styles.empty}>No matches</div>
      </div>
    );
  }

  return (
    <div className={styles.menu} data-testid="mention-menu" ref={menuRef}>
      {hasAgents && (
        <>
          <div className={styles.sectionHeader}>Agents</div>
          <ul className={styles.list}>
            {agentItems.map(item => {
              if (item.kind !== 'agent') return null;
              const myIndex = globalIndex++;
              const { participant } = item;
              return (
                <li key={participant.peerId}>
                  <button
                    type="button"
                    className={styles.item}
                    data-selected={myIndex === selectedIndex}
                    data-kind="agent"
                    data-menu-index={myIndex}
                    onClick={() => onSelect(item)}
                    style={{ '--agent-color': participant.color } as React.CSSProperties}
                  >
                    <span className={styles.agentDot} />
                    <span className={styles.name}>{participant.persona}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}

      {hasFiles && (
        <>
          <div className={styles.sectionHeader}>{hasAgents ? 'Files' : 'Files & Directories'}</div>
          <ul className={styles.list}>
            {fileItems.map(item => {
              if (item.kind !== 'file') return null;
              const myIndex = globalIndex++;
              return (
                <li key={item.entry.path}>
                  <button
                    type="button"
                    className={styles.item}
                    data-selected={myIndex === selectedIndex}
                    data-type={item.entry.type}
                    data-menu-index={myIndex}
                    onClick={() => {
                      if (item.entry.type === 'directory') {
                        onExpand(item);
                        return;
                      }
                      onSelect(item);
                    }}
                    onDoubleClick={() => {
                      if (item.entry.type === 'directory') {
                        onSelect(item);
                      }
                    }}
                  >
                    <span className={styles.indent} data-depth={item.depth} />
                    {item.entry.type === 'directory' ? (
                      myIndex === selectedIndex ? (
                        <FolderOpen className={styles.iconFolder} />
                      ) : (
                        <Folder className={styles.iconFolder} />
                      )
                    ) : (
                      <File className={styles.iconFile} />
                    )}
                    <span className={styles.name}>{item.entry.name}</span>
                    {item.entry.type === 'directory' && (
                      <span className={styles.typeBadge}>dir</span>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}

      {loading && (
        <div className={styles.loadingBar}>
          <Loader2 className={styles.spinnerSmall} />
        </div>
      )}
    </div>
  );
}
