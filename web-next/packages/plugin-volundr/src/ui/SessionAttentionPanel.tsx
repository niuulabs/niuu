import { ChevronRight, ExternalLink } from 'lucide-react';
import type { PermissionBehavior, PresentedFileRef, SessionAgent } from '@niuulabs/ui';
import type { TrackerIssue } from '../models/volundr.model';
import type { ChecklistItem, SessionAttentionItem } from '../domain/sessionAttention';
import { PanelSection } from './detail/PanelSection';
import {
  AgentsSection,
  ChangedFilesSection,
  DeliverablesSection,
  PlanSection,
  SessionInfoSection,
} from './detail/PanelSections';
import type { SessionInfoRow } from './detail/sessionHeaderModel';
import './SessionAttentionPanel.css';

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

/** Whether the panel has anything the session's other columns do not already show. */
export function sessionPanelHasContent({
  items,
  ticket,
  checklist,
  changedFiles,
  agents = [],
  deliverables = [],
  info = [],
}: {
  items: readonly SessionAttentionItem[];
  ticket?: TrackerIssue;
  checklist: readonly ChecklistItem[];
  changedFiles?: number;
  agents?: readonly SessionAgent[];
  deliverables?: readonly PresentedFileRef[];
  info?: readonly SessionInfoRow[];
}): boolean {
  return (
    items.length > 0 ||
    Boolean(ticket) ||
    checklist.length > 0 ||
    Boolean(changedFiles) ||
    agents.length > 0 ||
    deliverables.length > 0 ||
    info.length > 0
  );
}

/**
 * The session's side panel: what needs the operator, then the plan, the agents, the
 * changes, the deliverables and the facts about the session. A section with nothing to
 * show is not drawn.
 */
export function SessionAttentionPanel({
  items,
  ticket,
  checklist,
  agents = [],
  changedFiles,
  fileStats,
  deliverables = [],
  info = [],
  showOpenChat,
  onOpenChat,
  onOpenDiffs,
  onRespondPermission,
  onClose,
}: {
  items: readonly SessionAttentionItem[];
  ticket?: TrackerIssue;
  checklist: readonly ChecklistItem[];
  agents?: readonly SessionAgent[];
  changedFiles?: number;
  fileStats?: { added: number; modified: number; deleted: number } | null;
  deliverables?: readonly PresentedFileRef[];
  info?: readonly SessionInfoRow[];
  showOpenChat: boolean;
  onOpenChat: () => void;
  onOpenDiffs: () => void;
  onRespondPermission: (requestId: string, behavior: PermissionBehavior) => void;
  onClose: () => void;
}) {
  return (
    <aside
      className="niuu-session-panel"
      aria-label="Session overview"
      data-testid="session-attention-panel"
    >
      <div className="niuu-session-panel__head">
        <span>Overview</span>
        <button type="button" aria-label="Hide session panel" title="Hide" onClick={onClose}>
          <ChevronRight aria-hidden="true" />
        </button>
      </div>
      {items.length > 0 ? (
        <PanelSection title="Needs you" count={items.length}>
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
        </PanelSection>
      ) : null}

      <PlanSection checklist={checklist} />
      <AgentsSection agents={agents} />
      <ChangedFilesSection count={changedFiles} stats={fileStats} onOpenDiffs={onOpenDiffs} />
      <DeliverablesSection files={deliverables} />
      <SessionInfoSection rows={info} />

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
    </aside>
  );
}
