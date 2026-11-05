import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from '@tanstack/react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import {
  LoadingState,
  ErrorState,
  EmptyState,
  StateDot,
  cn,
  Dialog,
  DialogContent,
  Popover,
  PopoverContent,
  PopoverTrigger,
  Tooltip,
  TooltipProvider,
  SidebarResizer,
  useResizableSidebar,
  verdictLabel,
} from '@niuulabs/ui';
import type { DotState, Worker } from '@niuulabs/ui';
import {
  Archive,
  Pin,
  Plus,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  RotateCcw,
  Square,
  FolderOpen,
  MoreHorizontal,
  CircleDot,
  Download,
  FolderGit2,
  ListFilter,
  Search,
  SquareTerminal,
  Ticket,
  Trash2,
} from 'lucide-react';
import { LaunchWizard } from './LaunchWizard';
import { RenameSession } from './RenameSession';
import { AssignSessionProject } from './AssignSessionProject';
import { PinSession } from './PinSession';
import { usePinnedSessions } from './usePinnedSessions';
import { ImportExternalSessionsDialog } from './ImportExternalSessionsDialog';
import { useSessionList } from './hooks/useSessionStore';
import { useSessionRead } from './hooks/useSessionRead';
import { UnreadStream } from './UnreadStream';
import {
  buildSessionTree,
  groupByProject,
  isCoordinator,
  nestSessions,
  type SessionTreeNode,
} from '../domain/projectTree';
import { groupByState } from './sessions/groupByState';
import { LiveSessionDetailPage } from './LiveSessionDetailPage';
import { sessionHarness, type Session, type SessionState } from '../domain/session';
import { modelLabel as readableModel } from '@niuulabs/domain';
import type { IVolundrService } from '../ports/IVolundrService';
import { useForgePreference } from './useForgePreference';
import {
  FILTER_LABELS,
  SESSION_FILTERS,
  SESSION_STATE_LABELS,
  STOPPABLE_STATES,
  SIDEBAR_WIDTH,
  matchesSessionFilter,
  type SessionFilter,
} from './sessions/presentation';
import { sessionTimeFact, useMinuteClock, workedDuration } from './sessions/timeFact';
import './SessionsPage.css';

// ---------------------------------------------------------------------------
// Pod group definitions — maps display labels to session states
// ---------------------------------------------------------------------------

interface PodGroupDef {
  label: string;
  states: SessionState[];
}

type SidebarMode = 'state' | 'repo' | 'forge' | 'project';
const SIDEBAR_MODES = ['state', 'repo', 'forge', 'project'] as const;
const SIDEBAR_MODE_LABELS: Record<SidebarMode, string> = {
  state: 'State',
  repo: 'Repo',
  forge: 'Host',
  project: 'Project',
};
type RowAction = 'stop' | 'archive' | 'restore' | 'delete';
const DEFAULT_FILTER: SessionFilter = 'live';
/** The states a person checks often sit beside the search, as Lexi's Unread chip does. */
const QUICK_FILTERS: readonly SessionFilter[] = ['unread', 'attention'];
/** The rest are chosen from the list menu. */
const MENU_FILTERS = SESSION_FILTERS.filter((value) => !QUICK_FILTERS.includes(value));
type ReadChange = 'read' | 'unread';

interface SessionSection {
  id?: string;
  label: string;
  sessions: Session[];
}

const POD_GROUPS: PodGroupDef[] = [
  { label: 'NEEDS YOU', states: ['awaiting_input'] },
  { label: 'WORKING', states: ['running'] },
  { label: 'IDLE', states: ['idle', 'ready'] },
  { label: 'BOOTING', states: ['provisioning', 'requested'] },
  { label: 'ERROR', states: ['failed'] },
  { label: 'STOPPED', states: ['terminated', 'terminating'] },
  { label: 'ARCHIVED', states: ['archived'] },
];

// ---------------------------------------------------------------------------
// Session state → dot state mapping
// ---------------------------------------------------------------------------

const SESSION_DOT: Record<SessionState, DotState> = {
  running: 'running',
  idle: 'idle',
  awaiting_input: 'attention',
  provisioning: 'processing',
  requested: 'queued',
  ready: 'healthy',
  terminating: 'degraded',
  terminated: 'archived',
  archived: 'archived',
  failed: 'failed',
};

export function looksLikeRepoLabel(value: string): boolean {
  return (
    value.includes('#') ||
    value.startsWith('~/') ||
    value.startsWith('/') ||
    value.startsWith('http')
  );
}

export function compactSourceParts(value: string): { label: string; branch?: string } {
  if (value.includes('#')) {
    const [repo, branch] = value.split('#');
    return { label: shortenRepoLabel(repo ?? value), branch: branch || undefined };
  }
  return { label: shortenRepoLabel(value) };
}

export function shortenRepoLabel(value: string): string {
  if (value.startsWith('~/') || value.startsWith('/')) return value;
  const trimmed = value.replace(/\/+$/, '');
  const slug = trimmed.split('/').pop() ?? trimmed;
  return slug.replace(/\.git$/, '') || value;
}

export function toGroupTestId(label: string): string {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export function sessionActivityTs(session: Session): number {
  return new Date(session.lastActivityAt ?? session.startedAt).getTime();
}

const EXTERNAL_ORIGINS = new Set(['claude', 'codex']);

/** External origins ('claude' / 'codex') get a badge; volundr-native rows do not. */
export function sessionOriginBadge(session: Pick<Session, 'origin'>): string | null {
  if (!session.origin) return null;
  if (!EXTERNAL_ORIGINS.has(session.origin)) return null;
  return session.origin;
}

/** When the session's latest reply arrived; sessions with no timestamp sort last. */
function latestOutputTs(session: Session): number {
  const at = Date.parse(session.readState?.latestOutputAt ?? '');
  return Number.isFinite(at) ? at : 0;
}

export function compareSessionsByActivity(a: Session, b: Session): number {
  return sessionActivityTs(b) - sessionActivityTs(a);
}

export function repoGroupLabel(session: Session): string {
  if (session.preview && looksLikeRepoLabel(session.preview)) {
    return compactSourceParts(session.preview).label;
  }
  if (session.personaName.startsWith('~/') || session.personaName.startsWith('/')) {
    return session.personaName;
  }
  return 'other';
}

export function groupByRepo(sessions: Session[]): SessionSection[] {
  const grouped = new Map<string, Session[]>();

  for (const session of sessions) {
    const label = repoGroupLabel(session);
    const bucket = grouped.get(label);
    if (bucket) {
      bucket.push(session);
    } else {
      grouped.set(label, [session]);
    }
  }

  return [...grouped.entries()]
    .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
    .map(([label, groupedSessions]) => ({
      label,
      sessions: [...groupedSessions].sort(compareSessionsByActivity),
    }));
}

export function forgeGroupLabel(session: Session): string {
  return session.clusterName ?? session.clusterId ?? 'unknown host';
}

export function groupByForge(sessions: Session[]): SessionSection[] {
  const grouped = new Map<string, Session[]>();

  for (const session of sessions) {
    const label = forgeGroupLabel(session);
    const bucket = grouped.get(label);
    if (bucket) {
      bucket.push(session);
    } else {
      grouped.set(label, [session]);
    }
  }

  return [...grouped.entries()]
    .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
    .map(([label, groupedSessions]) => ({
      label,
      sessions: [...groupedSessions].sort(compareSessionsByActivity),
    }));
}

// ---------------------------------------------------------------------------
// PodEntry — a single session row in the sidebar
// ---------------------------------------------------------------------------

function PodEntry({
  session,
  selected,
  onSelect,
  collapsed = false,
  selectable = false,
  checked = false,
  onToggleSelection,
  showDetails = false,
  onAction,
  onReadChange,
  busy = false,
  readBusy = false,
  coordinatorWorkers,
  context = false,
  nested = false,
}: {
  session: Session;
  selected: boolean;
  onSelect: () => void;
  /** Set when this session leads others: how many workers it has. */
  coordinatorWorkers?: number;
  /** Listed only as the path to a worker that matches the filters. */
  context?: boolean;
  /** A worker listed under its coordinator: a compact row. */
  nested?: boolean;
  collapsed?: boolean;
  selectable?: boolean;
  checked?: boolean;
  onToggleSelection?: () => void;
  showDetails?: boolean;
  onAction?: (session: Session, action: RowAction) => void;
  onReadChange?: (session: Session, change: ReadChange) => void;
  busy?: boolean;
  readBusy?: boolean;
}) {
  const primaryLabel = session.name || session.personaName || session.id;
  const trackerLabel = session.sagaId ?? session.runId ?? session.trackerIssue?.identifier;
  const previewLabel = session.preview;
  const sourceParts =
    previewLabel && looksLikeRepoLabel(previewLabel) ? compactSourceParts(previewLabel) : null;
  const forgeLabel = session.clusterName ?? session.clusterId;
  const originBadge = sessionOriginBadge(session);
  const harness = sessionHarness(session);
  const modelLabel = session.model
    ? readableModel(session.model)
    : harness === 'claude'
      ? 'Claude'
      : 'Codex';
  const unread = session.readState?.isUnread === true;
  const now = useMinuteClock();
  const timeFact = sessionTimeFact(session, now);
  const [actionsOpen, setActionsOpen] = useState(false);
  const localPath =
    session.source?.type === 'local_mount'
      ? (session.source.local_path ?? session.source.path ?? session.source.paths?.[0]?.host_path)
      : undefined;
  const sourceLabel =
    session.source?.type === 'git'
      ? `${shortenRepoLabel(session.source.repo)}${session.source.branch ? ` @${session.source.branch}` : ''}`
      : (localPath ??
        (sourceParts
          ? `${sourceParts.label}${sourceParts.branch ? ` @${sourceParts.branch}` : ''}`
          : previewLabel));
  const isLocal =
    session.source?.type === 'local_mount' ||
    sourceLabel?.startsWith('/') ||
    sourceLabel?.startsWith('~/');
  const sourceText = sourceLabel?.replace(/^\/home\/[^/]+(?:\/|$)/, '~/');
  const sourceIcon = isLocal ? (
    <FolderOpen size={13} />
  ) : sourceParts || session.source?.type === 'git' ? (
    <FolderGit2 size={13} />
  ) : (
    <SquareTerminal size={13} />
  );
  return (
    <div
      className="forge-session-row"
      data-actions-open={actionsOpen}
      data-context={context || undefined}
      data-nested={nested || undefined}
    >
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? 'true' : undefined}
        title={context ? `${primaryLabel}: shown because a worker under it matches` : primaryLabel}
        data-testid={`pod-entry-${session.id}`}
        className={cn(
          'forge-session-row__select niuu:flex niuu:w-full niuu:items-start niuu:gap-2 niuu:border-b niuu:border-l-2 niuu:px-3 niuu:py-1.5 niuu:text-left niuu:transition-colors',
          selected
            ? 'niuu:border-brand niuu:border-b-white/10 forge-session-row__select--selected niuu:shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]'
            : 'niuu:border-transparent niuu:border-b-white/6 niuu:hover:bg-bg-tertiary',
        )}
      >
        {selectable && !collapsed ? (
          <span
            role="checkbox"
            tabIndex={0}
            aria-checked={checked}
            onClick={(event) => {
              event.stopPropagation();
              onToggleSelection?.();
            }}
            onKeyDown={(event) => {
              if (event.key !== 'Enter' && event.key !== ' ') return;
              event.preventDefault();
              event.stopPropagation();
              onToggleSelection?.();
            }}
            aria-label={`${checked ? 'Deselect' : 'Select'} stopped session ${session.id}`}
            data-testid={`stopped-session-checkbox-${session.id}`}
            className={cn(
              'niuu:mt-0.5 niuu:flex niuu:h-4 niuu:w-4 niuu:flex-shrink-0 niuu:items-center niuu:justify-center niuu:rounded-sm niuu:border niuu:transition-colors',
              checked
                ? 'niuu:border-brand niuu:bg-brand niuu:text-bg-primary'
                : 'niuu:border-border-subtle niuu:bg-bg-elevated niuu:text-transparent niuu:hover:border-brand/60',
            )}
          >
            <Check className="niuu:h-3 niuu:w-3" />
          </span>
        ) : null}
        {collapsed ? (
          <StateDot
            state={SESSION_DOT[session.state]}
            pulse={session.state === 'running' || session.state === 'awaiting_input'}
          />
        ) : (
          <div className="forge-session-row__content">
            <div className="forge-session-row__headline">
              <span className="forge-session-row__title">{primaryLabel}</span>
              {unread && (
                <span
                  className="forge-session-unread-dot"
                  role="img"
                  aria-label="Unread"
                  data-testid={`session-unread-dot-${session.id}`}
                />
              )}
            </div>
            {session.shortDescription ? (
              <div
                className="forge-session-row__preview"
                title={session.shortDescription}
                data-testid={`session-preview-${session.id}`}
              >
                {session.shortDescription}
              </div>
            ) : (
              sourceLabel && (
                <div
                  className="forge-session-row__preview forge-session-row__source"
                  title={localPath ?? previewLabel ?? sourceLabel}
                  data-testid={`session-preview-${session.id}`}
                >
                  {sourceIcon}
                  <span>{sourceText}</span>
                </div>
              )
            )}
            <div className="forge-session-row__metadata">
              <span
                className="forge-session-harness"
                data-harness={harness}
                title={originBadge ? `Imported from ${originBadge}` : session.model}
                data-testid={originBadge ? `session-origin-badge-${session.id}` : undefined}
              >
                {modelLabel}
              </span>
              {forgeLabel && (
                <span className="forge-session-row__host" title={`Host: ${forgeLabel}`}>
                  {forgeLabel}
                </span>
              )}
              {coordinatorWorkers !== undefined && (
                <span
                  className="forge-session-row__leads"
                  data-testid={`session-leads-${session.id}`}
                >
                  {coordinatorWorkers === 0
                    ? 'Coordinator'
                    : `Coordinator · ${coordinatorWorkers} ${coordinatorWorkers === 1 ? 'worker' : 'workers'}`}
                </span>
              )}
              {timeFact && (
                <span
                  className="forge-session-row__age"
                  title={timeFact}
                  data-testid={`session-time-fact-${session.id}`}
                >
                  {timeFact}
                </span>
              )}
              <span className="forge-session-state" data-state={session.state}>
                {SESSION_STATE_LABELS[session.state]}
              </span>
            </div>
            {showDetails && (
              <div className="forge-session-row__details">
                {trackerLabel && (
                  <span title={trackerLabel}>
                    <Ticket size={12} />
                    {trackerLabel}
                  </span>
                )}
                {session.shortDescription && sourceLabel && (
                  <span title={localPath ?? previewLabel ?? sourceLabel}>
                    {sourceIcon}
                    {sourceText}
                  </span>
                )}
              </div>
            )}
          </div>
        )}
      </button>
      {!collapsed && onAction && (
        <button
          type="button"
          className="forge-session-row__more"
          aria-label={`Actions for ${primaryLabel}`}
          aria-expanded={actionsOpen}
          aria-controls={`session-actions-${session.id}`}
          onClick={() => setActionsOpen(!actionsOpen)}
        >
          <MoreHorizontal size={18} />
        </button>
      )}
      {!collapsed && onAction && (
        <div
          id={`session-actions-${session.id}`}
          className="forge-session-row__actions"
          aria-label={`Actions for ${primaryLabel}`}
        >
          <RenameSession sessionId={session.id} name={primaryLabel} disabled={busy} />
          <PinSession sessionId={session.id} name={primaryLabel} />
          {session.readState && onReadChange && (
            <button
              type="button"
              disabled={readBusy}
              title={unread ? 'Mark as read' : 'Mark as unread'}
              aria-label={`${unread ? 'Mark as read' : 'Mark as unread'}: ${primaryLabel}`}
              data-testid={`pod-entry-${session.id}-read-toggle`}
              onClick={() => onReadChange(session, unread ? 'read' : 'unread')}
            >
              {unread ? <CheckCheck /> : <CircleDot />}
            </button>
          )}
          <AssignSessionProject
            sessionId={session.id}
            name={primaryLabel}
            instanceId={session.clusterId}
            disabled={busy}
          />
          {STOPPABLE_STATES.has(session.state) && (
            <button
              type="button"
              disabled={busy}
              title="Stop session"
              data-action="stop"
              aria-label={`Stop ${primaryLabel}`}
              data-testid={`pod-entry-${session.id}-stop`}
              onClick={() => onAction(session, 'stop')}
            >
              <Square />
            </button>
          )}
          {session.state !== 'archived' && session.state !== 'terminating' && (
            <button
              type="button"
              disabled={busy}
              title="Archive session"
              aria-label={`Archive ${primaryLabel}`}
              data-testid={`pod-entry-${session.id}-archive`}
              onClick={() => onAction(session, 'archive')}
            >
              <Archive />
            </button>
          )}
          {session.state === 'archived' && (
            <button
              type="button"
              disabled={busy}
              title="Restore session"
              aria-label={`Restore ${primaryLabel}`}
              onClick={() => onAction(session, 'restore')}
            >
              <RotateCcw />
            </button>
          )}
          <button
            type="button"
            disabled={busy}
            title="Delete session"
            data-action="delete"
            aria-label={`Delete ${primaryLabel}`}
            data-testid={`pod-entry-${session.id}-delete`}
            onClick={() => onAction(session, 'delete')}
          >
            <Trash2 />
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PodGroup — a state section with label + session entries
// ---------------------------------------------------------------------------

/** The open session's room: its workers, the one the chat shows alone, and how to pick one. */
interface OpenRoom {
  sessionId: string;
  workers: readonly Worker[];
  focusedPeerId: string | null;
  onSelect: (sessionId: string, peerId: string) => void;
}

const WORKER_STATUS_TEXT: Record<Worker['status'], string> = {
  working: 'Working',
  idle: 'Idle',
  error: 'Error',
};

/** A worker in the open session's room, under its coordinator: picking it shows only its output. */
function RoomWorkerRow({
  worker,
  selected,
  onSelect,
}: {
  worker: Worker;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <div className="forge-session-row forge-room-worker" data-nested="true">
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected ? 'true' : undefined}
        title={`${worker.name}: show only this worker`}
        data-testid={`room-worker-${worker.peerId}`}
        className={cn(
          'forge-session-row__select niuu:flex niuu:w-full niuu:items-center niuu:gap-2 niuu:border-b niuu:border-l-2 niuu:px-3 niuu:py-1.5 niuu:text-left niuu:transition-colors',
          selected
            ? 'niuu:border-brand niuu:border-b-white/10 forge-session-row__select--selected'
            : 'niuu:border-transparent niuu:border-b-white/6 niuu:hover:bg-bg-tertiary',
        )}
        style={{ '--forge-worker-color': worker.color } as React.CSSProperties}
      >
        <span className="forge-room-worker__dot" aria-hidden="true" />
        <span className="forge-session-row__title">{worker.name}</span>
        <span className="forge-room-worker__state" data-status={worker.status}>
          {worker.latest && worker.status !== 'working'
            ? verdictLabel(worker.latest.verdict)
            : WORKER_STATUS_TEXT[worker.status]}
        </span>
      </button>
    </div>
  );
}

function SessionTreeEntry({
  node,
  renderRow,
  depth = 0,
  openRoom,
}: {
  node: SessionTreeNode;
  renderRow: (session: Session, depth: number) => React.ReactNode;
  depth?: number;
  openRoom?: OpenRoom | null;
}) {
  const roomWorkers = openRoom?.sessionId === node.session.id ? openRoom.workers : [];
  const hasChildren = node.children.length > 0 || roomWorkers.length > 0;
  const [folded, setFolded] = useForgePreference(
    `tree.${node.session.clusterId}.${node.session.id}`,
    '0',
    ['0', '1'],
  );
  return (
    <div className="forge-session-tree-entry">
      <div className="forge-session-tree-row">
        {hasChildren && (
          <button
            type="button"
            className="forge-session-tree-toggle"
            aria-label={`${folded === '1' ? 'Expand' : 'Collapse'} workers of ${node.session.name ?? node.session.personaName}`}
            aria-expanded={folded !== '1'}
            onClick={() => setFolded(folded === '1' ? '0' : '1')}
          >
            {folded === '1' ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
          </button>
        )}
        {renderRow(node.session, depth)}
      </div>
      {folded !== '1' && hasChildren && (
        <div className="forge-session-tree-children">
          {openRoom &&
            roomWorkers.map((worker) => (
              <RoomWorkerRow
                key={worker.peerId}
                worker={worker}
                selected={openRoom.focusedPeerId === worker.peerId}
                onSelect={() => openRoom.onSelect(node.session.id, worker.peerId)}
              />
            ))}
          {node.children.map((child) => (
            <SessionTreeEntry
              key={child.session.id}
              node={child}
              renderRow={renderRow}
              depth={depth + 1}
              openRoom={openRoom}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function PodGroup({
  groupKey,
  pinned = false,
  hierarchical = false,
  label,
  sessions,
  selectedId,
  onSelect,
  collapsed = false,
  selectableSessionIds,
  selectedSessionIds,
  onToggleSelection,
  showDetails = false,
  onAction,
  onReadChange,
  busyId,
  readPendingIds,
  contextIds,
  workerCounts,
  openRoom = null,
}: {
  contextIds?: ReadonlySet<string>;
  workerCounts?: ReadonlyMap<string, number>;
  /** The open session's room workers, listed under it in the tree. */
  openRoom?: OpenRoom | null;
  groupKey?: string;
  pinned?: boolean;
  hierarchical?: boolean;
  label: string;
  sessions: Session[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  collapsed?: boolean;
  selectableSessionIds?: ReadonlySet<string>;
  selectedSessionIds?: ReadonlySet<string>;
  onToggleSelection?: (id: string) => void;
  showDetails?: boolean;
  onAction?: (session: Session, action: RowAction) => void;
  onReadChange?: (session: Session, change: ReadChange) => void;
  busyId?: string;
  readPendingIds?: ReadonlySet<string>;
}) {
  const [folded, setFolded] = useForgePreference(`group.${groupKey ?? label}`, '0', ['0', '1']);
  if (sessions.length === 0) return null;

  const roomWorkerCount = (s: Session) =>
    openRoom?.sessionId === s.id ? openRoom.workers.length : 0;
  const renderRow = (s: Session, depth = 0) => (
    <PodEntry
      key={s.id}
      session={s}
      coordinatorWorkers={
        (workerCounts && isCoordinator(s, workerCounts)) || roomWorkerCount(s) > 0
          ? (workerCounts?.get(s.id) ?? 0) + roomWorkerCount(s)
          : undefined
      }
      context={contextIds?.has(s.id) ?? false}
      nested={depth > 0}
      showDetails={showDetails}
      onAction={onAction}
      onReadChange={onReadChange}
      busy={!!busyId}
      readBusy={readPendingIds?.has(s.id) ?? false}
      selected={s.id === selectedId && !(openRoom?.sessionId === s.id && openRoom.focusedPeerId)}
      onSelect={() => onSelect(s.id)}
      collapsed={collapsed}
      selectable={selectableSessionIds?.has(s.id) ?? false}
      checked={selectedSessionIds?.has(s.id) ?? false}
      onToggleSelection={
        onToggleSelection && selectableSessionIds?.has(s.id)
          ? () => onToggleSelection(s.id)
          : undefined
      }
    />
  );

  return (
    <div
      data-testid={`pod-group-${toGroupTestId(label)}`}
      className={pinned ? 'forge-session-pinned-group' : undefined}
    >
      {!collapsed && (
        <button
          type="button"
          aria-expanded={folded !== '1'}
          onClick={() => setFolded(folded === '1' ? '0' : '1')}
          className="niuu:flex niuu:w-full niuu:items-center niuu:justify-between niuu:border-b niuu:border-white/6 niuu:px-4 niuu:py-2 niuu:text-[10px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.18em] niuu:text-text-muted"
        >
          <span className="niuu:flex niuu:items-center niuu:gap-1">
            {folded === '1' ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
            {pinned && <Pin size={12} />}
            {label}
          </span>
          <span
            className="niuu:font-mono niuu:text-text-faint"
            data-testid={`pod-group-${toGroupTestId(label)}-count`}
          >
            {sessions.length}
          </span>
        </button>
      )}
      {(collapsed || folded !== '1') &&
        (hierarchical && !collapsed
          ? buildSessionTree(sessions).map((node) => (
              <SessionTreeEntry
                key={node.session.id}
                node={node}
                renderRow={renderRow}
                openRoom={openRoom}
              />
            ))
          : sessions.map((s) => renderRow(s)))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SessionsPage — master-detail layout
// ---------------------------------------------------------------------------

export function SessionsPage() {
  const navigate = useNavigate();
  const { sessionId: routeSessionId } = useParams({ strict: false });
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  // The open session's room workers, heard from its chat, and the one it shows on its own.
  const [room, setRoom] = useState<{ sessionId: string; workers: readonly Worker[] } | null>(null);
  const [focusedWorker, setFocusedWorker] = useState<{ sessionId: string; peerId: string } | null>(
    null,
  );
  const [searchQuery, setSearchQuery] = useState('');
  const { ids: pinnedIds, pinned } = usePinnedSessions();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarMode, setSidebarMode] = useForgePreference<SidebarMode>(
    'grouping',
    'state',
    SIDEBAR_MODES,
  );
  const [filter, setFilter] = useForgePreference<SessionFilter>(
    'filter',
    DEFAULT_FILTER,
    SESSION_FILTERS,
  );
  const [details, setDetails] = useForgePreference('details', '0', ['0', '1']);
  const [tokens, setTokens] = useForgePreference('tokens', '0', ['0', '1']);
  const { width, resizer } = useResizableSidebar('niuu.forge.sidebarWidth', SIDEBAR_WIDTH);
  const [rowBusyId, setRowBusyId] = useState<string>();
  const [actionError, setActionError] = useState<string>();
  const [deleteTarget, setDeleteTarget] = useState<Session | null>(null);
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [stoppedSelectionMode, setStoppedSelectionMode] = useState(false);
  const [selectedStoppedIds, setSelectedStoppedIds] = useState<Set<string>>(new Set());
  const [deleteStoppedOpen, setDeleteStoppedOpen] = useState(false);
  const [launchOpen, setLaunchOpen] = useState(false);
  const [listMenuOpen, setListMenuOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();
  const sessionRead = useSessionRead();
  const projects = useQuery({
    queryKey: ['volundr', 'projects'],
    queryFn: () => volundr.getProjects(),
    enabled: sidebarMode === 'project',
    staleTime: 30_000,
  });

  const sessionsQuery = useSessionList();
  const connectionStates = sessionsQuery.sources.filter(
    (source) =>
      !source.archived ||
      !sessionsQuery.sources.some(
        (other) => other.id === source.id && !other.archived && (other.loading || other.error),
      ),
  );
  const allSessions = useMemo(() => sessionsQuery.data ?? [], [sessionsQuery.data]);
  const stoppedSessionCount = useMemo(
    () => allSessions.filter((session) => session.state === 'terminated').length,
    [allSessions],
  );
  const stoppedSessionIds = useMemo(
    () =>
      new Set(
        allSessions
          .filter((session) => session.state === 'terminated')
          .map((session) => session.id),
      ),
    [allSessions],
  );

  const filterCounts = useMemo(
    () =>
      Object.fromEntries(
        SESSION_FILTERS.map((value) => [
          value,
          allSessions.filter((session) => matchesSessionFilter(session, value)).length,
        ]),
      ) as Record<SessionFilter, number>,
    [allSessions],
  );

  // Filter by search query
  const searchedSessions = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return allSessions;
    return allSessions.filter(
      (s) =>
        s.id.toLowerCase().includes(q) ||
        s.name?.toLowerCase().includes(q) ||
        s.personaName.toLowerCase().includes(q) ||
        s.preview?.toLowerCase().includes(q) ||
        s.clusterName?.toLowerCase().includes(q) ||
        s.clusterId?.toLowerCase().includes(q),
    );
  }, [allSessions, searchQuery]);
  const pinnedSessions = useMemo(() => {
    const byId = new Map(searchedSessions.map((session) => [session.id, session]));
    return pinnedIds.flatMap((id) => {
      const session = byId.get(id);
      return session ? [session] : [];
    });
  }, [searchedSessions, pinnedIds]);
  const filteredSessions = useMemo(
    () => searchedSessions.filter((session) => matchesSessionFilter(session, filter)),
    [searchedSessions, filter],
  );
  const unpinnedSessions = useMemo(
    () => filteredSessions.filter((session) => !pinned.has(session.id)),
    [filteredSessions, pinned],
  );
  const filteredStoppedSessions = useMemo(
    () => filteredSessions.filter((session) => session.state === 'terminated'),
    [filteredSessions],
  );
  const filteredStoppedIds = useMemo(
    () => new Set(filteredStoppedSessions.map((session) => session.id)),
    [filteredStoppedSessions],
  );

  // The stream reads newest output first.
  const unreadSessions = useMemo(
    () =>
      filteredSessions
        .filter((session) => session.readState?.isUnread === true)
        .sort((a, b) => latestOutputTs(b) - latestOutputTs(a)),
    [filteredSessions],
  );

  // The 24-hour work total exists only where a host records turn spans; never guess the rest.
  const workToday = useMemo(() => {
    const reported = allSessions.filter((session) => typeof session.workedSeconds24h === 'number');
    if (reported.length === 0) return null;
    const worked = reported.filter((session) => (session.workedSeconds24h ?? 0) > 0);
    return {
      sessions: worked.length,
      seconds: worked.reduce((total, session) => total + (session.workedSeconds24h ?? 0), 0),
    };
  }, [allSessions]);
  const listedSessionCount = useMemo(
    () => allSessions.filter((session) => session.state !== 'archived').length,
    [allSessions],
  );

  // Workers follow their coordinator into its group, in every grouping; a coordinator the
  // filters leave out stays listed (dimmed) as the path to a worker that matches.
  const nesting = useMemo(
    () =>
      nestSessions(
        unpinnedSessions,
        allSessions.filter((session) => !pinned.has(session.id)),
        allSessions,
      ),
    [unpinnedSessions, allSessions, pinned],
  );

  // Build sidebar groups from the roots, then put each root's workers back under it.
  const sidebarGroups = useMemo<SessionSection[]>(() => {
    const roots = nesting.roots;
    const sections = ((): SessionSection[] => {
      if (sidebarMode === 'project') return groupByProject(roots, projects.data ?? []);
      if (sidebarMode === 'repo') return groupByRepo(roots);
      if (sidebarMode === 'forge') return groupByForge(roots);
      const grouped = groupByState(roots);
      return POD_GROUPS.map((g) => ({
        label: g.label,
        sessions: g.states.flatMap((st) => grouped[st]),
      }));
    })();
    return sections.map((section) => ({
      ...section,
      sessions: section.sessions.flatMap((root) => nesting.subtree(root)),
    }));
  }, [nesting, sidebarMode, projects.data]);
  const requestedSessionId = typeof routeSessionId === 'string' ? routeSessionId : null;
  const resolvedSelectedSessionId = useMemo(() => {
    if (allSessions.length === 0) return null;
    if (requestedSessionId) {
      const matchingSession = allSessions.find((session) => session.id === requestedSessionId);
      return matchingSession?.id ?? null;
    }
    if (selectedSessionId) {
      const matchingSession = allSessions.find((session) => session.id === selectedSessionId);
      if (matchingSession) return matchingSession.id;
    }
    const running = allSessions.find((session) => session.state === 'running');
    return running?.id ?? allSessions[0]?.id ?? null;
  }, [allSessions, requestedSessionId, selectedSessionId]);
  const handleRoomWorkers = useCallback(
    (workers: readonly Worker[]) => {
      if (resolvedSelectedSessionId) setRoom({ sessionId: resolvedSelectedSessionId, workers });
    },
    [resolvedSelectedSessionId],
  );
  const focusedPeerId =
    focusedWorker?.sessionId === resolvedSelectedSessionId ? focusedWorker.peerId : null;
  const handleFocusWorker = useCallback(
    (peerId: string | null) =>
      setFocusedWorker(
        peerId && resolvedSelectedSessionId
          ? { sessionId: resolvedSelectedSessionId, peerId }
          : null,
      ),
    [resolvedSelectedSessionId],
  );
  const openRoom = useMemo<OpenRoom | null>(
    () =>
      room && room.sessionId === resolvedSelectedSessionId && room.workers.length > 0
        ? {
            sessionId: room.sessionId,
            workers: room.workers,
            focusedPeerId,
            onSelect: (sessionId, peerId) => setFocusedWorker({ sessionId, peerId }),
          }
        : null,
    [room, resolvedSelectedSessionId, focusedPeerId],
  );
  const resolvedSelectedStoppedIds = useMemo(
    () => new Set([...selectedStoppedIds].filter((id) => stoppedSessionIds.has(id))),
    [selectedStoppedIds, stoppedSessionIds],
  );

  // The unread stream stands in for the detail while that filter is on and no session is open.
  const showUnreadStream = filter === 'unread' && !requestedSessionId;
  const openSession = useMemo(
    () =>
      showUnreadStream
        ? undefined
        : allSessions.find((session) => session.id === resolvedSelectedSessionId),
    [allSessions, resolvedSelectedSessionId, showUnreadStream],
  );
  // Opening an unread session reads it, but only one the person opened (a click or a link):
  // the session shown by default when the page loads has not been read. Once per opening: a
  // session the person marks unread while it stays open must not be read again behind their back.
  const chosenSession =
    openSession && (openSession.id === requestedSessionId || openSession.id === selectedSessionId)
      ? openSession
      : undefined;
  const openedSessionId = useRef<string | null>(null);
  const { markRead: markSessionRead } = sessionRead;
  useEffect(() => {
    if (!chosenSession) {
      openedSessionId.current = null;
      return;
    }
    if (openedSessionId.current === chosenSession.id) return;
    openedSessionId.current = chosenSession.id;
    if (chosenSession.readState?.isUnread) void markSessionRead(chosenSession);
  }, [chosenSession, markSessionRead]);

  function handleReadChange(session: Session, change: ReadChange) {
    void (change === 'read' ? sessionRead.markRead(session) : sessionRead.markUnread(session));
  }

  function handleSelectFilter(value: SessionFilter) {
    setFilter(value);
    if (value !== 'unread' || !requestedSessionId) return;
    // The stream shows only while no session is open.
    setSelectedSessionId(null);
    void navigate({ to: '/volundr/sessions' });
  }

  function toggleStoppedSelection() {
    const next = !stoppedSelectionMode;
    setStoppedSelectionMode(next);
    if (next) {
      setFilter('stopped');
      setSelectedStoppedIds(new Set(stoppedSessionIds));
    } else setSelectedStoppedIds(new Set());
  }

  function handleSelectSession(id: string) {
    setFocusedWorker(null);
    setSelectedSessionId(id);
    void navigate({
      to: '/volundr/sessions/$sessionId',
      params: { sessionId: id },
    });
  }

  async function handleArchiveAllStopped() {
    if (archiveBusy || stoppedSessionCount === 0) return;
    setArchiveBusy(true);
    setActionError(undefined);
    try {
      await volundr.archiveStoppedSessions();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'history'] }),
      ]);
      await sessionsQuery.refetch();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Could not archive stopped sessions');
    } finally {
      setArchiveBusy(false);
    }
  }

  function handleToggleStoppedSelection(sessionId: string) {
    setSelectedStoppedIds((current) => {
      const next = new Set(current);
      if (next.has(sessionId)) {
        next.delete(sessionId);
      } else {
        next.add(sessionId);
      }
      return next;
    });
  }

  function handleSelectAllVisibleStopped() {
    setSelectedStoppedIds(new Set(filteredStoppedSessions.map((session) => session.id)));
  }

  function handleClearStoppedSelection() {
    setSelectedStoppedIds(new Set());
  }

  async function handleDeleteSelectedStopped() {
    if (deleteBusy || resolvedSelectedStoppedIds.size === 0) return;
    const ids = [...resolvedSelectedStoppedIds];
    setDeleteBusy(true);
    setActionError(undefined);
    try {
      const results = await Promise.allSettled(ids.map((id) => volundr.deleteSession(id)));
      const deletedIds = ids.filter((_, index) => results[index]?.status === 'fulfilled');
      const failures = results.filter((result) => result.status === 'rejected');
      if (failures.length > 0) {
        setSelectedStoppedIds(new Set(ids.filter((id) => !deletedIds.includes(id))));
        await queryClient.invalidateQueries({ queryKey: ['volundr'] });
        if (resolvedSelectedSessionId && deletedIds.includes(resolvedSelectedSessionId)) {
          setSelectedSessionId(null);
          await navigate({ to: '/volundr/sessions', replace: true });
        }
        throw new Error(
          `${failures.length} session(s) could not be deleted. ${failures[0]?.reason instanceof Error ? failures[0].reason.message : 'Try again.'}`,
        );
      }
      if (resolvedSelectedSessionId && ids.includes(resolvedSelectedSessionId)) {
        setSelectedSessionId(null);
        await navigate({ to: '/volundr/sessions', replace: true });
      }
      setDeleteStoppedOpen(false);
      setStoppedSelectionMode(false);
      setSelectedStoppedIds(new Set());
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'history'] }),
      ]);
      await sessionsQuery.refetch();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : 'Could not delete stopped sessions');
    } finally {
      setDeleteBusy(false);
    }
  }

  async function handleRowAction(session: Session, action: RowAction, confirmed = false) {
    if (rowBusyId || archiveBusy || deleteBusy) return;
    if (action === 'delete' && !confirmed) {
      setActionError(undefined);
      setDeleteTarget(session);
      return;
    }
    setActionError(undefined);
    setRowBusyId(session.id);
    try {
      if (action === 'stop') await volundr.stopSession(session.id);
      if (action === 'archive') {
        if (STOPPABLE_STATES.has(session.state)) await volundr.stopSession(session.id);
        await volundr.archiveSession(session.id);
      }
      if (action === 'restore') await volundr.restoreSession(session.id);
      if (action === 'delete') {
        await volundr.deleteSession(session.id);
        setDeleteTarget(null);
        if (session.id === resolvedSelectedSessionId) {
          setSelectedSessionId(null);
          await navigate({ to: '/volundr/sessions', replace: true });
        }
      }
      await queryClient.invalidateQueries({ queryKey: ['volundr'] });
    } catch (error) {
      setActionError(error instanceof Error ? error.message : `Could not ${action} session`);
    } finally {
      setRowBusyId(undefined);
    }
  }

  const deleteSelectionCount = resolvedSelectedStoppedIds.size;
  const deleteSelectionPreview = filteredStoppedSessions.filter((session) =>
    resolvedSelectedStoppedIds.has(session.id),
  );

  return (
    <>
      <div
        className="forge-sessions niuu:relative niuu:flex niuu:h-full"
        data-detail-open={!!requestedSessionId}
        data-testid="sessions-page"
      >
        {/* ── Left sidebar: pod list ─────────────────────────────── */}
        <nav
          className={cn('forge-session-list niuu:relative niuu:shrink-0 niuu:overflow-hidden')}
          style={
            sidebarCollapsed
              ? {
                  width: '48px',
                  minWidth: '48px',
                  maxWidth: '48px',
                  flexBasis: '48px',
                }
              : {
                  width: `${width}px`,
                  minWidth: `${SIDEBAR_WIDTH.min}px`,
                  maxWidth: `${SIDEBAR_WIDTH.max}px`,
                  flexBasis: `${width}px`,
                }
          }
          aria-label="Session list"
          data-testid="pod-list-sidebar"
        >
          {sidebarCollapsed ? (
            <div className="niuu:flex niuu:h-full niuu:flex-col niuu:overflow-hidden">
              <div className="niuu:flex niuu:items-center niuu:justify-center niuu:border-b niuu:border-border-subtle niuu:py-2.5">
                <button
                  type="button"
                  onClick={() => setSidebarCollapsed(false)}
                  className="niuu:font-mono niuu:text-sm niuu:text-text-muted"
                  data-testid="pod-sidebar-toggle"
                  aria-label="Expand pods sidebar"
                >
                  ›
                </button>
              </div>
              <div className="niuu:flex-1 niuu:overflow-y-auto niuu:py-2">
                <PodGroup
                  label="Pinned"
                  groupKey="pinned"
                  pinned
                  sessions={pinnedSessions}
                  selectedId={resolvedSelectedSessionId}
                  onSelect={handleSelectSession}
                  collapsed
                />
                {sidebarGroups.map((g) => (
                  <PodGroup
                    key={g.id ?? g.label}
                    label={g.label}
                    groupKey={g.id ?? `${sidebarMode}:${g.label}`}
                    hierarchical
                    openRoom={openRoom}
                    contextIds={nesting.contextIds}
                    workerCounts={nesting.workerCounts}
                    sessions={g.sessions}
                    selectedId={resolvedSelectedSessionId}
                    onSelect={handleSelectSession}
                    showDetails={details === '1'}
                    onAction={(session, action) => void handleRowAction(session, action)}
                    busyId={rowBusyId}
                    collapsed
                  />
                ))}
              </div>
            </div>
          ) : (
            <div className="niuu:flex niuu:h-full niuu:flex-col niuu:overflow-hidden">
              <div className="forge-session-head">
                <h2>Sessions</h2>
                <TooltipProvider>
                  <div className="forge-session-heading-actions">
                    <Popover open={listMenuOpen} onOpenChange={setListMenuOpen}>
                      <PopoverTrigger asChild>
                        <button
                          type="button"
                          data-testid="session-list-menu"
                          aria-label="Filter and group sessions"
                          title="Filter and group sessions"
                        >
                          <ListFilter size={18} />
                        </button>
                      </PopoverTrigger>
                      <PopoverContent side="bottom" align="start" className="forge-session-menu">
                        <section aria-label="Show">
                          <h3>Show</h3>
                          {MENU_FILTERS.map((value) => {
                            const count = filterCounts[value];
                            // An empty state is noise; the chosen one always shows.
                            if (count === 0 && filter !== value) return null;
                            return (
                              <button
                                key={value}
                                type="button"
                                aria-pressed={filter === value}
                                data-testid={`session-filter-${value}`}
                                data-filter={value}
                                onClick={() => handleSelectFilter(value)}
                              >
                                <span>{FILTER_LABELS[value]}</span>
                                <span>{count}</span>
                              </button>
                            );
                          })}
                        </section>
                        <section aria-label="Group by" data-testid="pod-group-mode">
                          <h3>Group by</h3>
                          {SIDEBAR_MODES.map((mode) => (
                            <button
                              key={mode}
                              type="button"
                              aria-pressed={sidebarMode === mode}
                              data-testid={`pod-group-mode-${mode}`}
                              onClick={() => setSidebarMode(mode)}
                            >
                              {SIDEBAR_MODE_LABELS[mode]}
                            </button>
                          ))}
                        </section>
                        <section aria-label="Display">
                          <h3>Display</h3>
                          <label>
                            <input
                              type="checkbox"
                              checked={details === '1'}
                              onChange={(event) => setDetails(event.target.checked ? '1' : '0')}
                              aria-label="Show session details"
                            />
                            Details
                          </label>
                          <label>
                            <input
                              type="checkbox"
                              checked={tokens === '1'}
                              onChange={(event) => setTokens(event.target.checked ? '1' : '0')}
                              aria-label="Show token usage"
                            />
                            Token usage
                          </label>
                        </section>
                        {stoppedSessionCount > 0 && (
                          <section aria-label="Manage stopped sessions">
                            <h3>Stopped sessions · {stoppedSessionCount}</h3>
                            <button
                              type="button"
                              data-testid="toggle-stopped-selection-button"
                              aria-pressed={stoppedSelectionMode}
                              onClick={() => {
                                toggleStoppedSelection();
                                setListMenuOpen(false);
                              }}
                            >
                              <Check size={14} />
                              {stoppedSelectionMode ? 'Cancel selection' : 'Select all stopped'}
                            </button>
                            <button
                              type="button"
                              disabled={archiveBusy || deleteBusy}
                              data-testid="archive-stopped-button"
                              onClick={() => void handleArchiveAllStopped()}
                            >
                              <Archive size={14} />
                              {archiveBusy ? 'Archiving…' : 'Archive all stopped'}
                            </button>
                          </section>
                        )}
                      </PopoverContent>
                    </Popover>
                    <Tooltip
                      content="Launch a new session"
                      delayMs={100}
                      className="forge-session-action-tooltip"
                    >
                      <button
                        type="button"
                        className="forge-session-launch"
                        onClick={() => setLaunchOpen(true)}
                        data-testid="pod-launch-button"
                        aria-label="Launch a new session"
                      >
                        <Plus size={20} strokeWidth={2} />
                      </button>
                    </Tooltip>
                    <Tooltip
                      content="Import CLI sessions"
                      delayMs={100}
                      className="forge-session-action-tooltip"
                    >
                      <button
                        type="button"
                        onClick={() => setImportOpen(true)}
                        data-testid="pod-import-button"
                        aria-label="Import external CLI sessions"
                      >
                        <Download size={18} />
                      </button>
                    </Tooltip>
                    <button
                      type="button"
                      onClick={() => setSidebarCollapsed(true)}
                      data-testid="pod-sidebar-toggle"
                      aria-label="Collapse pods sidebar"
                    >
                      <ChevronRight className="forge-session-collapse" size={19} />
                    </button>
                  </div>
                </TooltipProvider>
              </div>

              <div className="forge-session-search-row">
                <div className="forge-session-search">
                  <Search aria-hidden="true" />
                  <input
                    type="search"
                    placeholder="Find a session"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    data-testid="pod-search"
                    aria-label="Filter sessions"
                  />
                </div>
              </div>
              <div className="forge-session-quick" role="group" aria-label="Session states">
                {QUICK_FILTERS.map((value) => {
                  const count = filterCounts[value];
                  if (count === 0 && filter !== value) return null;
                  return (
                    <button
                      key={value}
                      type="button"
                      aria-pressed={filter === value}
                      data-testid={`session-filter-${value}`}
                      data-filter={value}
                      onClick={() => handleSelectFilter(filter === value ? DEFAULT_FILTER : value)}
                    >
                      <span>{FILTER_LABELS[value]}</span>
                      <span>{count}</span>
                    </button>
                  );
                })}
                <span className="forge-session-scope" data-testid="session-list-scope">
                  {FILTER_LABELS[filter]} · {SIDEBAR_MODE_LABELS[sidebarMode]}
                </span>
              </div>

              {connectionStates.some((source) => source.loading || source.error) && (
                <div className="forge-session-source-status" aria-label="Host connections">
                  {connectionStates
                    .filter((source) => source.loading || source.error)
                    .map((source) => (
                      <p
                        key={`${source.id}:${source.archived}`}
                        role="status"
                        title={source.error ?? undefined}
                      >
                        <span>
                          {source.name}
                          {source.archived ? ' archive' : ''}:{' '}
                          {source.error
                            ? source.stale
                              ? 'unavailable · showing saved list'
                              : 'unavailable'
                            : 'loading…'}
                        </span>
                      </p>
                    ))}
                  {connectionStates.some((source) => source.error) && (
                    <button type="button" onClick={() => void sessionsQuery.refetch()}>
                      Retry host connections
                    </button>
                  )}
                </div>
              )}

              <div className="forge-session-scroll niuu:flex-1 niuu:overflow-y-auto niuu:pb-1.5">
                <PodGroup
                  label="Pinned"
                  groupKey="pinned"
                  pinned
                  workerCounts={nesting.workerCounts}
                  sessions={pinnedSessions}
                  selectedId={resolvedSelectedSessionId}
                  onSelect={handleSelectSession}
                  showDetails={details === '1'}
                  onAction={(session, action) => void handleRowAction(session, action)}
                  onReadChange={handleReadChange}
                  readPendingIds={sessionRead.pendingIds}
                  busyId={rowBusyId}
                  selectableSessionIds={stoppedSelectionMode ? filteredStoppedIds : undefined}
                  selectedSessionIds={resolvedSelectedStoppedIds}
                  onToggleSelection={handleToggleStoppedSelection}
                />
                {sidebarMode === 'project' && projects.isLoading && (
                  <p className="forge-session-project-status" role="status">
                    Loading projects…
                  </p>
                )}
                {sidebarMode === 'project' && projects.isError && (
                  <p className="forge-session-project-status" role="status">
                    Project names could not be refreshed. Sessions remain available.
                  </p>
                )}
                {filteredSessions.length === 0 &&
                  pinnedSessions.length === 0 &&
                  !sessionsQuery.isLoading &&
                  !sessionsQuery.isError && (
                    <p className="niuu:p-4 niuu:text-sm niuu:text-text-muted">
                      No sessions match these filters.
                    </p>
                  )}
                {sidebarGroups.map((g) => (
                  <PodGroup
                    key={g.id ?? g.label}
                    label={g.label}
                    groupKey={g.id ?? `${sidebarMode}:${g.label}`}
                    hierarchical
                    openRoom={openRoom}
                    contextIds={nesting.contextIds}
                    workerCounts={nesting.workerCounts}
                    sessions={g.sessions}
                    selectedId={resolvedSelectedSessionId}
                    onSelect={handleSelectSession}
                    showDetails={details === '1'}
                    onAction={(session, action) => void handleRowAction(session, action)}
                    onReadChange={handleReadChange}
                    readPendingIds={sessionRead.pendingIds}
                    busyId={rowBusyId}
                    selectableSessionIds={stoppedSelectionMode ? filteredStoppedIds : undefined}
                    selectedSessionIds={resolvedSelectedStoppedIds}
                    onToggleSelection={handleToggleStoppedSelection}
                  />
                ))}
              </div>
              <footer
                role="group"
                className="forge-session-footer"
                aria-label="Session list settings"
              >
                {actionError && (
                  <div role="alert" className="niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-critical">
                    {actionError}
                  </div>
                )}
                {sessionRead.error && (
                  <div
                    role="alert"
                    className="forge-session-read-error niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-critical"
                    data-testid="session-read-error"
                  >
                    <span>{sessionRead.error}</span>
                    <button type="button" onClick={sessionRead.clearError}>
                      Dismiss
                    </button>
                  </div>
                )}
                {stoppedSelectionMode && (
                  <div className="forge-session-maintenance" aria-label="Select stopped sessions">
                    <div className="forge-session-footer-actions">
                      <button
                        type="button"
                        onClick={handleSelectAllVisibleStopped}
                        disabled={filteredStoppedSessions.length === 0}
                        data-testid="select-all-stopped-button"
                      >
                        Select visible ({filteredStoppedSessions.length})
                      </button>
                      <button
                        type="button"
                        onClick={handleClearStoppedSelection}
                        disabled={deleteSelectionCount === 0}
                        data-testid="clear-stopped-selection-button"
                      >
                        Clear
                      </button>
                      <button
                        type="button"
                        className="forge-session-delete"
                        onClick={() => setDeleteStoppedOpen(true)}
                        disabled={deleteSelectionCount === 0}
                        data-testid="delete-selected-stopped-button"
                      >
                        <Trash2 size={14} />
                        Delete selected ({deleteSelectionCount})
                      </button>
                      <button
                        type="button"
                        data-testid="cancel-stopped-selection-button"
                        onClick={toggleStoppedSelection}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
                <p className="forge-session-today" data-testid="sessions-today">
                  {workToday
                    ? workToday.sessions === 0
                      ? 'Last 24 h · no work recorded'
                      : `Last 24 h · ${workToday.sessions} ${workToday.sessions === 1 ? 'session' : 'sessions'} · ${workedDuration(workToday.seconds)}`
                    : `${listedSessionCount} ${listedSessionCount === 1 ? 'session' : 'sessions'}`}
                </p>
              </footer>
            </div>
          )}
        </nav>

        {/* ── Main content: session detail ───────────────────────── */}
        {!sidebarCollapsed && (
          <SidebarResizer
            label="Resize session list"
            className="forge-session-resizer"
            {...resizer}
          />
        )}

        <div className="forge-session-detail niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col niuu:overflow-hidden">
          <button
            type="button"
            className="forge-session-back"
            onClick={() => {
              setSelectedSessionId(null);
              void navigate({ to: '/volundr/sessions' });
            }}
          >
            ‹ Sessions
          </button>
          {sessionsQuery.isLoading && <LoadingState label="Loading sessions…" />}
          {sessionsQuery.isError && (
            <ErrorState
              title="Failed to load sessions"
              message={
                sessionsQuery.error instanceof Error ? sessionsQuery.error.message : 'Unknown error'
              }
            />
          )}
          {sessionsQuery.data && showUnreadStream && (
            <UnreadStream sessions={unreadSessions} onOpen={handleSelectSession} />
          )}
          {sessionsQuery.data &&
            !showUnreadStream &&
            !resolvedSelectedSessionId &&
            requestedSessionId &&
            connectionStates.some((source) => source.loading) && (
              <LoadingState label="Loading selected session…" />
            )}
          {sessionsQuery.data &&
            !showUnreadStream &&
            !resolvedSelectedSessionId &&
            !(requestedSessionId && connectionStates.some((source) => source.loading)) && (
              <EmptyState
                title="No session selected"
                description="Select a session from the sidebar."
              />
            )}
          {sessionsQuery.data && !showUnreadStream && resolvedSelectedSessionId && (
            <LiveSessionDetailPage
              key={resolvedSelectedSessionId}
              sessionId={resolvedSelectedSessionId}
              focusedWorkerId={focusedPeerId}
              onFocusWorker={handleFocusWorker}
              onRoomWorkers={handleRoomWorkers}
            />
          )}
        </div>
      </div>
      <Dialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open && !rowBusyId) setDeleteTarget(null);
        }}
      >
        <DialogContent
          title="Delete session?"
          description={`Permanently delete ${deleteTarget?.name || deleteTarget?.personaName || 'this session'} and its session record? This cannot be undone.`}
        >
          {actionError && <p role="alert">{actionError}</p>}
          <div className="niuu:flex niuu:justify-end niuu:gap-3">
            <button type="button" disabled={!!rowBusyId} onClick={() => setDeleteTarget(null)}>
              Cancel
            </button>
            <button
              type="button"
              disabled={!!rowBusyId}
              className="niuu:text-critical"
              onClick={() => {
                if (deleteTarget) void handleRowAction(deleteTarget, 'delete', true);
              }}
            >
              Delete permanently
            </button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog
        open={deleteStoppedOpen}
        onOpenChange={(open) => {
          if (!deleteBusy) setDeleteStoppedOpen(open);
        }}
      >
        <DialogContent
          title="Delete Selected Stopped Sessions"
          description="This removes the selected stopped sessions from the list. This action cannot be undone."
        >
          <div className="niuu:space-y-4">
            {actionError && <p role="alert">{actionError}</p>}
            <div className="niuu:rounded-lg niuu:border niuu:border-red-500/25 niuu:bg-red-500/8 niuu:p-3 niuu:text-sm niuu:text-text-secondary">
              {deleteSelectionCount === 1
                ? 'Delete 1 stopped session?'
                : `Delete ${deleteSelectionCount} stopped sessions?`}
            </div>
            {deleteSelectionPreview.length > 0 ? (
              <div className="niuu:max-h-48 niuu:space-y-2 niuu:overflow-y-auto niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:p-3">
                {deleteSelectionPreview.map((session) => (
                  <div
                    key={session.id}
                    className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3 niuu:font-mono niuu:text-xs niuu:text-text-secondary"
                  >
                    <span className="niuu:truncate">{session.personaName || session.id}</span>
                    <span className="niuu:text-text-faint">{session.id}</span>
                  </div>
                ))}
              </div>
            ) : null}
            <div className="niuu:flex niuu:justify-end niuu:gap-2">
              <button
                type="button"
                onClick={() => setDeleteStoppedOpen(false)}
                className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary niuu:hover:bg-bg-secondary"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleDeleteSelectedStopped()}
                disabled={deleteBusy || deleteSelectionCount === 0}
                className="niuu:rounded-md niuu:bg-red-500 niuu:px-3 niuu:py-2 niuu:text-sm niuu:font-medium niuu:text-white niuu:hover:opacity-90 niuu:disabled:cursor-not-allowed niuu:disabled:opacity-50"
                data-testid="confirm-delete-selected-stopped-button"
              >
                {deleteBusy ? 'Deleting…' : `Delete ${deleteSelectionCount}`}
              </button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
      <LaunchWizard open={launchOpen} onOpenChange={setLaunchOpen} />
      <ImportExternalSessionsDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        onImported={async () => {
          await sessionsQuery.refetch();
        }}
      />
    </>
  );
}
