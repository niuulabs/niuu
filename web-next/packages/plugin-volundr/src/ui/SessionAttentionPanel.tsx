import { Check, Circle, CircleDot, ExternalLink, FileDiff } from 'lucide-react';
import type { PermissionBehavior, RoomParticipant } from '@niuulabs/ui';
import type { TrackerIssue } from '../models/volundr.model';
import type { ChecklistItem, SessionAttentionItem } from '../domain/sessionAttention';
import './SessionAttentionPanel.css';

const CHECKLIST_ICON = {
  completed: Check,
  in_progress: CircleDot,
  pending: Circle,
} as const;

const ITEM_LABEL: Record<SessionAttentionItem['kind'], string> = {
  gate: 'Your decision',
  permission: 'Permission',
  input: 'Question',
};

function AttentionRow({
  item,
  showOpenChat,
  onOpenChat,
  onRespondPermission,
}: {
  item: SessionAttentionItem;
  showOpenChat: boolean;
  onOpenChat: () => void;
  onRespondPermission: (requestId: string, behavior: PermissionBehavior) => void;
}) {
  return (
    <li className="niuu-session-panel__item" data-testid={`attention-${item.kind}`}>
      <span className="niuu-session-panel__eyebrow">{ITEM_LABEL[item.kind]}</span>
      <strong>{item.title}</strong>
      {item.detail ? <p>{item.detail}</p> : null}
      <div className="niuu-session-panel__actions">
        {item.kind === 'permission' ? (
          <>
            <button
              type="button"
              aria-label={`Allow ${item.title} once`}
              onClick={() => onRespondPermission(item.id, 'allow_once')}
            >
              Allow once
            </button>
            <button
              type="button"
              aria-label={`Deny ${item.title}`}
              onClick={() => onRespondPermission(item.id, 'deny')}
            >
              Deny
            </button>
          </>
        ) : null}
        {showOpenChat ? (
          <button type="button" onClick={onOpenChat}>
            Open chat
          </button>
        ) : null}
      </div>
    </li>
  );
}

function PanelSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="niuu-session-panel__section" aria-label={title}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}

export function SessionAttentionPanel({
  items,
  peers,
  ticket,
  checklist,
  changedFiles,
  showOpenChat,
  onOpenChat,
  onOpenDiffs,
  onRespondPermission,
}: {
  items: readonly SessionAttentionItem[];
  peers: readonly RoomParticipant[];
  ticket?: TrackerIssue;
  checklist: readonly ChecklistItem[];
  changedFiles?: number;
  showOpenChat: boolean;
  onOpenChat: () => void;
  onOpenDiffs: () => void;
  onRespondPermission: (requestId: string, behavior: PermissionBehavior) => void;
}) {
  const doneCount = checklist.filter((item) => item.status === 'completed').length;
  return (
    <aside
      className="niuu-session-panel"
      aria-label="Session overview"
      data-testid="session-attention-panel"
    >
      <PanelSection title={items.length > 0 ? `Needs you (${items.length})` : 'Needs you'}>
        {items.length > 0 ? (
          <ul>
            {items.map((item) => (
              <AttentionRow
                key={`${item.kind}:${item.id}`}
                item={item}
                showOpenChat={showOpenChat}
                onOpenChat={onOpenChat}
                onRespondPermission={onRespondPermission}
              />
            ))}
          </ul>
        ) : (
          <p className="niuu-session-panel__quiet">Nothing is waiting on you.</p>
        )}
      </PanelSection>

      {ticket ? (
        <PanelSection title="Ticket">
          <div className="niuu-session-panel__ticket">
            {ticket.url ? (
              <a href={ticket.url} target="_blank" rel="noreferrer">
                {ticket.identifier}
                <ExternalLink aria-hidden="true" />
              </a>
            ) : (
              <span>{ticket.identifier}</span>
            )}
            <strong>{ticket.title}</strong>
            <small>{ticket.status}</small>
          </div>
        </PanelSection>
      ) : null}

      {checklist.length > 0 ? (
        <PanelSection title={`Tasks (${doneCount}/${checklist.length})`}>
          <ul className="niuu-session-panel__checklist">
            {checklist.map((item, index) => {
              const Icon = CHECKLIST_ICON[item.status];
              return (
                <li key={`${index}:${item.label}`} data-status={item.status}>
                  <Icon aria-hidden="true" />
                  <span>{item.label}</span>
                </li>
              );
            })}
          </ul>
        </PanelSection>
      ) : null}

      {peers.length > 0 ? (
        <PanelSection title="Running">
          <ul className="niuu-session-panel__peers">
            {peers.map((peer) => (
              <li key={peer.peerId}>
                <span>{peer.displayName || peer.persona}</span>
                {peer.status ? <small>{peer.status}</small> : null}
              </li>
            ))}
          </ul>
        </PanelSection>
      ) : null}

      {changedFiles ? (
        <PanelSection title="Changes">
          <button type="button" className="niuu-session-panel__link" onClick={onOpenDiffs}>
            <FileDiff aria-hidden="true" />
            {changedFiles} file{changedFiles === 1 ? '' : 's'} changed
          </button>
        </PanelSection>
      ) : null}
    </aside>
  );
}
