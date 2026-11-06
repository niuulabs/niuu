import type { ReactNode } from 'react';
import { FileDiff, FolderOpen, X } from 'lucide-react';
import './RightDock.css';

export type DockPane = 'changes' | 'files';

const PANES: Array<{ id: DockPane; label: string; icon: typeof FileDiff }> = [
  { id: 'changes', label: 'Changes', icon: FileDiff },
  { id: 'files', label: 'Files', icon: FolderOpen },
];

/**
 * Lexi's right dock: Changes and Files beside the conversation, one at a time under a slim
 * tab strip, so a diff or a file stays open while the person reads and replies.
 */
export function RightDock({
  pane,
  width,
  edge,
  onSelect,
  onClose,
  children,
}: {
  pane: DockPane;
  /** The dock's width in pixels: the person's, narrowed to leave the conversation room. */
  width: number;
  /** The control on the dock's left edge, such as a resizer; it takes none of the width. */
  edge?: ReactNode;
  onSelect: (pane: DockPane) => void;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <aside
      className="forge-right-dock"
      aria-label="Right dock"
      data-testid="right-dock"
      style={{ width: `${width}px` }}
    >
      {edge}
      <div className="forge-right-dock__tabs" role="tablist" aria-label="Panels">
        {PANES.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={pane === id}
            className="forge-right-dock__tab"
            data-testid={`right-dock-tab-${id}`}
            onClick={() => onSelect(id)}
          >
            <Icon aria-hidden="true" />
            {label}
          </button>
        ))}
        <button
          type="button"
          className="forge-right-dock__close"
          aria-label="Close the dock"
          onClick={onClose}
        >
          <X aria-hidden="true" />
        </button>
      </div>
      <div className="forge-right-dock__body" role="tabpanel">
        {children}
      </div>
    </aside>
  );
}
