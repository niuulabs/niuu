import type { ReactNode } from 'react';
import { Plus, X, type LucideIcon } from 'lucide-react';
import { SessionMoreMenu, type MoreMenuItem } from './SessionMoreMenu';
import './RightDock.css';

export interface RightDockTab {
  id: string;
  label: string;
  icon: LucideIcon;
  /** The full name when the label is shortened, such as a file's path. */
  title?: string;
}

const ADD_TRIGGER = {
  icon: Plus,
  label: 'Add a pane',
  className: 'forge-right-dock__add',
  testId: 'right-dock-add',
};

/**
 * Lexi's right dock: panes the person opens from "+" beside the conversation, one shown at a
 * time under a strip of their tabs, each closable, so a diff, a file or a side chat stays open
 * while the person reads and replies.
 */
export function RightDock({
  tabs,
  active,
  width,
  edge,
  addItems,
  onSelect,
  onClosePane,
  onClose,
  children,
}: {
  tabs: readonly RightDockTab[];
  active: string | null;
  /** The dock's width in pixels: the person's, narrowed to leave the conversation room. */
  width: number;
  /** The control on the dock's left edge, such as a resizer; it takes none of the width. */
  edge?: ReactNode;
  /** What "+" opens. */
  addItems: readonly MoreMenuItem[];
  onSelect: (id: string) => void;
  onClosePane: (id: string) => void;
  /** Hides the dock; its panes are kept for when it comes back. */
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
      <div className="forge-right-dock__tabs">
        <div className="forge-right-dock__strip" role="tablist" aria-label="Panes">
          {tabs.map(({ id, label, icon: Icon, title }) => (
            <div key={id} className="forge-right-dock__tab-wrap" data-selected={active === id}>
              <button
                type="button"
                role="tab"
                aria-selected={active === id}
                className="forge-right-dock__tab"
                data-testid={`right-dock-tab-${id}`}
                title={title ?? label}
                onClick={() => onSelect(id)}
              >
                <Icon aria-hidden="true" />
                <span>{label}</span>
              </button>
              <button
                type="button"
                className="forge-right-dock__tab-close"
                aria-label={`Close ${label}`}
                onClick={() => onClosePane(id)}
              >
                <X aria-hidden="true" />
              </button>
            </div>
          ))}
          <SessionMoreMenu items={addItems} trigger={ADD_TRIGGER} />
        </div>
        <button
          type="button"
          className="forge-right-dock__close"
          aria-label="Close the dock"
          title="Close the dock (⌥⌘→)"
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
