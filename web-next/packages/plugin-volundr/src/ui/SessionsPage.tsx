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
  useDictation,
  useResizableSidebar,
  verdictLabel,
} from '@niuulabs/ui';
import type { DotState, Worker } from '@niuulabs/ui';
import {
  Archive,
  AudioLines,
  Pin,
  Plus,
  Check,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  RotateCcw,
  Square,
  FolderOpen,
  MoreHorizontal,
  CircleDot,
  Download,
  Folder,
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
  isCoordinationRole,
  isCoordinator,
  leadLabel,
  nestSessions,
  roleLabel,
  type SessionTreeNode,
} from '../domain/projectTree';
import { groupByState } from './sessions/groupByState';
import { LiveSessionDetailPage } from './LiveSessionDetailPage';
import { SessionStartupHeadline } from './SessionStartupProgress';
import { useElementWidth } from './detail/useElementWidth';
import { ProjectHome } from './ProjectHome';
import { sessionHarness, type Session, type SessionState } from '../domain/session';
import type { IVolundrService } from '../ports/IVolundrService';
import { useForgePreference } from './useForgePreference';
import {
  DOCK_WIDTH,
  FILTER_LABELS,
  SESSION_FILTERS,
  SESSION_STATE_LABELS,
  STOPPABLE_STATES,
  SIDEBAR_WIDTH,
  TRANSCRIPT_MIN_WIDTH,
  matchesSessionFilter,
  sessionModelLabel,
  type SessionFilter,
} from './sessions/presentation';
import {
  compactDuration,
  sessionTimeFact,
  useMinuteClock,
  workedDuration,
} from './sessions/timeFact';
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

const HEADLINE_PHRASES: Record<SessionState, string> = {
  running: 'is working',
  idle: 'is idle',
  ready: 'is ready',
  awaiting_input: 'is waiting for you',
  provisioning: 'is starting',
  requested: 'is queued',
  terminating: 'is stopping',
  terminated: 'stopped',
  failed: 'stopped with an error',
  archived: 'was archived',
};

/** A project folder's second line: what its latest session did, as Lexi heads a folder. */
export function projectHeadline(sessions: readonly Session[]): string {
  const latest = [...sessions].sort((a, b) => sessionActivityTs(b) - sessionActivityTs(a))[0];
  if (!latest) return 'No sessions yet';
  const state = SESSION_STATE_LABELS[latest.state];
  if (latest.shortDescription) return `${state} · ${latest.shortDescription}`;
  const name = latest.name || latest.personaName || latest.id;
  return `${state} · ${name} ${HEADLINE_PHRASES[latest.state]}`;
}

/**
 * An unread row's note: "New · 4h" for a reply not read yet, "Marked unread" for one the
 * person marked. Forge counts events, not replies, so the row does not claim a number.
 */
export function unreadLabel(session: Session, now: number): string {
  const read = session.readState;
  if (read?.manuallyUnread && read.latestOutputSeq <= read.readThroughSeq) return 'Marked unread';
  const at = Date.parse(read?.latestOutputAt ?? '');
  return Number.isFinite(at) ? `New · ${compactDuration(Math.max(0, now - at))}` : 'New';
}

/** What a project folder says about all of its sessions, whatever the filters show. */
interface ProjectFolder {
  headline: string;
  needsYou: number;
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
  unreadNote = false,
}: {
  /** In the unread list the row says it is new, and since when, instead of its state. */
  unreadNote?: boolean;
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
  // A run's member leads with its role: its name repeats the run's and is cut off first.
  const memberRole =
    session.coordination?.parent && !isCoordinationRole(session.coordination.role)
      ? roleLabel(session.coordination.role)
      : null;
  const trackerLabel = session.sagaId ?? session.runId ?? session.trackerIssue?.identifier;
  const previewLabel = session.preview;
  const sourceParts =
    previewLabel && looksLikeRepoLabel(previewLabel) ? compactSourceParts(previewLabel) : null;
  const forgeLabel = session.clusterName ?? session.clusterId;
  const originBadge = sessionOriginBadge(session);
  const harness = sessionHarness(session);
  const modelLabel = sessionModelLabel(session);
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
      data-context={(context && !selected) || undefined}
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
              <span
                className="forge-session-row__dot"
                data-state={session.state}
                aria-hidden="true"
              />
              {memberRole && (
                <span
                  className="forge-session-row__role"
                  data-testid={`pod-entry-${session.id}-role`}
                >
                  {memberRole}
                </span>
              )}
              <span className="forge-session-row__title">{primaryLabel}</span>
              {unread && (
                <span
                  className="forge-session-unread-dot"
                  role="img"
                  aria-label="Unread"
                  data-testid={`session-unread-dot-${session.id}`}
                />
              )}
              {memberRole && (
                <span
                  className="forge-session-state forge-session-row__member-state"
                  data-state={session.state}
                  title={timeFact ?? undefined}
                  data-testid={`pod-entry-${session.id}-state`}
                >
                  {SESSION_STATE_LABELS[session.state]}
                </span>
              )}
            </div>
            {/* Two lines, as in Lexi: the workspace waits for Details; an agent's own
                summary of the work is worth a line of its own. */}
            {session.shortDescription && (
              <div
                className="forge-session-row__preview"
                title={session.shortDescription}
                data-testid={`session-preview-${session.id}`}
              >
                {session.shortDescription}
              </div>
            )}
            {/* A starting (or failed-to-start) session says which launch stage it is on. */}
            {session.startup &&
              (session.state === 'requested' ||
                session.state === 'provisioning' ||
                session.state === 'failed') && (
                <SessionStartupHeadline
                  startup={session.startup}
                  status={session.state}
                  error={session.error}
                  className={cn(
                    'forge-session-row__preview',
                    session.state === 'failed' && 'niuu:text-critical',
                  )}
                />
              )}
            {/* Lexi's second line: host · model · role, then the state on the right. A run's
                member has its state on its one line; its host and model are its run's. */}
            {!memberRole && (
              <div className="forge-session-row__metadata">
                {forgeLabel && (
                  <span className="forge-session-row__host" title={`Host: ${forgeLabel}`}>
                    {forgeLabel}
                  </span>
                )}
                <span
                  className="forge-session-harness"
                  data-harness={harness}
                  title={originBadge ? `Imported from ${originBadge}` : session.model}
                  data-testid={originBadge ? `session-origin-badge-${session.id}` : undefined}
                >
                  {modelLabel}
                </span>
                {coordinatorWorkers !== undefined && (
                  <span
                    className="forge-session-row__leads"
                    data-testid={`session-leads-${session.id}`}
                  >
                    {leadLabel(session, coordinatorWorkers)}
                  </span>
                )}
                {unreadNote ? (
                  <span className="forge-session-new" data-testid={`session-new-${session.id}`}>
                    {unreadLabel(session, now)}
                  </span>
                ) : (
                  <span
                    className="forge-session-state"
                    data-state={session.state}
                    title={timeFact ?? undefined}
                  >
                    {SESSION_STATE_LABELS[session.state]}
                  </span>
                )}
              </div>
            )}
            {showDetails && (
              <div className="forge-session-row__details">
                {timeFact && (
                  <span title={timeFact} data-testid={`session-time-fact-${session.id}`}>
                    {timeFact}
                  </span>
                )}
                {trackerLabel && (
                  <span title={trackerLabel}>
                    <Ticket size={12} />
                    {trackerLabel}
                  </span>
                )}
                {sourceLabel && (
                  <span
                    title={localPath ?? previewLabel ?? sourceLabel}
                    data-testid={`session-source-${session.id}`}
                  >
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
  /** Workers that are sessions of their own (a workflow run's members), nested under the room. */
  sessionWorkerIds: ReadonlySet<string>;
  focusedPeerId: string | null;
  onSelect: (sessionId: string, peerId: string) => void;
}

const WORKER_STATUS_TEXT: Record<Worker['status'], string> = {
  working: 'Working',
  idle: 'Idle',
  error: 'Error',
  left: 'Left',
  stopped: 'Stopped',
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
        {worker.persona && worker.persona !== worker.peerId && worker.persona !== worker.name && (
          <span
            className="forge-session-row__role"
            data-testid={`room-worker-${worker.peerId}-role`}
          >
            {roleLabel(worker.persona)}
          </span>
        )}
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
  // Under the open room, a nested session that is one of its workers (a workflow run's
  // member) is listed as the room's worker: its verdict, and picking it shows its work there.
  const roomWorkerIds = new Set(roomWorkers.map((worker) => worker.peerId));
  const children = node.children.filter((child) => !roomWorkerIds.has(child.session.id));
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
          {children.map((child) => (
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
  folder,
  folderSelected = false,
  onOpenFolder,
  sectionLabel = false,
}: {
  /** Draw the group as a project folder: name, needs-you badge, count and headline. */
  folder?: ProjectFolder;
  /** The folder's project home is open: the folder is filled, as Lexi's selected folder. */
  folderSelected?: boolean;
  /** Open the folder's project home; the caret only folds. */
  onOpenFolder?: () => void;
  /** Draw the group as a quiet section label, as Lexi heads its independent sessions. */
  sectionLabel?: boolean;
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
  // A project folder stays listed with nothing in it; other groups only hold sessions.
  if (sessions.length === 0 && !folder) return null;

  // A worker that is also a nested session is counted once, with the nested sessions.
  const roomWorkerCount = (s: Session) =>
    openRoom?.sessionId === s.id
      ? openRoom.workers.filter((worker) => !openRoom.sessionWorkerIds.has(worker.peerId)).length
      : 0;
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
      className={cn(
        pinned && 'forge-session-pinned-group',
        folder && 'forge-project-group',
        sectionLabel && 'forge-session-section',
      )}
    >
      {!collapsed && folder && (
        <div className="forge-project-folder" data-selected={folderSelected || undefined}>
          <button
            type="button"
            className="forge-project-folder__caret"
            aria-expanded={folded !== '1'}
            aria-label={`${folded === '1' ? 'Expand' : 'Collapse'} ${label}`}
            onClick={() => setFolded(folded === '1' ? '0' : '1')}
          >
            {folded === '1' ? (
              <ChevronRight size={13} aria-hidden="true" />
            ) : (
              <ChevronDown size={13} aria-hidden="true" />
            )}
          </button>
          <button
            type="button"
            className="forge-project-folder__open"
            aria-current={folderSelected ? 'true' : undefined}
            title={`Open ${label}`}
            onClick={onOpenFolder}
          >
            <Folder className="forge-project-folder__icon" size={15} aria-hidden="true" />
            <span className="forge-project-folder__name">{label}</span>
            {folder.needsYou > 0 && (
              <span
                className="forge-project-folder__badge"
                title={`${folder.needsYou} waiting for you`}
                data-testid={`pod-group-${toGroupTestId(label)}-needs-you`}
              >
                {folder.needsYou}
              </span>
            )}
            <span
              className="forge-project-folder__count"
              data-testid={`pod-group-${toGroupTestId(label)}-count`}
            >
              {sessions.length}
            </span>
            <span className="forge-project-folder__headline">{folder.headline}</span>
          </button>
        </div>
      )}
      {!collapsed && sectionLabel && (
        <button
          type="button"
          className="forge-session-section-label"
          aria-expanded={folded !== '1'}
          onClick={() => setFolded(folded === '1' ? '0' : '1')}
        >
          {label}
        </button>
      )}
      {!collapsed && folder && folded !== '1' && sessions.length === 0 && (
        <p className="forge-project-folder__empty">No sessions in this filter</p>
      )}
      {!collapsed && !folder && !sectionLabel && (
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
  // The project whose home fills the detail pane; a session and a project never both show.
  const [openProjectId, setOpenProjectId] = useState<string | null>(null);
  // The open session's room workers, heard from its chat, and the one it shows on its own.
  const [room, setRoom] = useState<{ sessionId: string; workers: readonly Worker[] } | null>(null);
  const [focusedWorker, setFocusedWorker] = useState<{ sessionId: string; peerId: string } | null>(
    null,
  );
  const [searchQuery, setSearchQuery] = useState('');
  // Lexi's "Search by voice": what is said becomes the search.
  const voiceSearch = useDictation((text) => setSearchQuery(text));
  const { ids: pinnedIds, pinned } = usePinnedSessions();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  // Projects first, as Lexi's code sidebar: each project a folder, then the sessions in none.
  // A new key, so a grouping saved while State was the default does not hold the list there.
  const [sidebarMode, setSidebarMode] = useForgePreference<SidebarMode>(
    'sessionGrouping',
    'project',
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
  // As Lexi's: the list folds to its rail when the open dock has no room beside the
  // conversation, and returns when the dock closes or the window widens. Expanding it while
  // folded keeps it open, and the dock steps aside instead, until the dock no longer needs it.
  const [dockOpen, setDockOpen] = useState(false);
  const [page, setPage] = useState<HTMLDivElement | null>(null);
  const pageWidth = useElementWidth(page);
  const dockNeedsList =
    dockOpen && pageWidth !== null && pageWidth - width - TRANSCRIPT_MIN_WIDTH < DOCK_WIDTH.min;
  const [listKeptOpen, setListKeptOpen] = useState(false);
  if (listKeptOpen && !dockNeedsList) setListKeptOpen(false);
  const listFolded = sidebarCollapsed || (dockNeedsList && !listKeptOpen);
  const expandList = () => {
    setSidebarCollapsed(false);
    if (dockNeedsList) setListKeptOpen(true);
  };
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
      if (sidebarMode === 'project')
        return groupByProject(roots, projects.data ?? [], { includeEmpty: true });
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
  // Folders speak for every listed session of their project, not only the filtered ones.
  const projectFolders = useMemo(() => {
    const byProject = new Map<string, Session[]>();
    for (const session of allSessions) {
      const id = session.coordination?.projectId;
      if (!id || session.state === 'archived') continue;
      byProject.set(id, [...(byProject.get(id) ?? []), session]);
    }
    const ids = new Set([
      ...(projects.data ?? []).map((project) => project.id),
      ...byProject.keys(),
    ]);
    return new Map<string, ProjectFolder>(
      [...ids].map((id) => {
        const sessions = byProject.get(id) ?? [];
        return [
          id,
          {
            headline: projectHeadline(sessions),
            needsYou: sessions.filter((session) => session.state === 'awaiting_input').length,
          },
        ];
      }),
    );
  }, [allSessions, projects.data]);
  const requestedSessionId = typeof routeSessionId === 'string' ? routeSessionId : null;
  const showProjectHome = openProjectId !== null;
  // Opening a session from a link or the browser's history leaves the project.
  const [seenRoute, setSeenRoute] = useState(requestedSessionId);
  if (seenRoute !== requestedSessionId) {
    setSeenRoute(requestedSessionId);
    if (requestedSessionId) setOpenProjectId(null);
  }
  const openProjectGroup = sidebarGroups.find((group) => group.id === openProjectId);
  const openProjectRecord = (projects.data ?? []).find((project) => project.id === openProjectId);
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
  // Only a session that leads its room lists the room's workers under it. A run's member
  // sees the run's other members in its own chat, but it leads none of them: listing them
  // under it would move the run's sessions every time a member is opened.
  const openSessionIsNested = useMemo(
    () =>
      Boolean(
        allSessions.find((session) => session.id === resolvedSelectedSessionId)?.coordination
          ?.parent,
      ),
    [allSessions, resolvedSelectedSessionId],
  );
  const openRoom = useMemo<OpenRoom | null>(
    () =>
      !showProjectHome &&
      !openSessionIsNested &&
      room &&
      room.sessionId === resolvedSelectedSessionId &&
      room.workers.length > 0
        ? {
            sessionId: room.sessionId,
            workers: room.workers,
            sessionWorkerIds: new Set(
              room.workers
                .filter((worker) => allSessions.some((session) => session.id === worker.peerId))
                .map((worker) => worker.peerId),
            ),
            focusedPeerId,
            onSelect: (sessionId, peerId) => setFocusedWorker({ sessionId, peerId }),
          }
        : null,
    [
      room,
      resolvedSelectedSessionId,
      focusedPeerId,
      showProjectHome,
      openSessionIsNested,
      allSessions,
    ],
  );
  // While a project's home is open no session is the open one.
  const listSelectedId = showProjectHome ? null : resolvedSelectedSessionId;
  const resolvedSelectedStoppedIds = useMemo(
    () => new Set([...selectedStoppedIds].filter((id) => stoppedSessionIds.has(id))),
    [selectedStoppedIds, stoppedSessionIds],
  );

  // The unread stream stands in for the detail while that filter is on and no session is open.
  const showUnreadStream = filter === 'unread' && !requestedSessionId && !showProjectHome;
  const openSession = useMemo(
    () =>
      showUnreadStream || showProjectHome
        ? undefined
        : allSessions.find((session) => session.id === resolvedSelectedSessionId),
    [allSessions, resolvedSelectedSessionId, showUnreadStream, showProjectHome],
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
    if (value === 'unread') setOpenProjectId(null);
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

  function handleOpenProject(id: string) {
    setFocusedWorker(null);
    setSelectedSessionId(null);
    setOpenProjectId(id);
    void navigate({ to: '/volundr/sessions' });
  }

  function handleSelectSession(id: string) {
    setFocusedWorker(null);
    setOpenProjectId(null);
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
        ref={setPage}
        className="forge-sessions niuu:relative niuu:flex niuu:h-full"
        data-detail-open={!!requestedSessionId || showProjectHome}
        data-testid="sessions-page"
      >
        {/* ── Left sidebar: pod list ─────────────────────────────── */}
        <nav
          className={cn('forge-session-list niuu:relative niuu:shrink-0 niuu:overflow-hidden')}
          style={
            listFolded
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
          {listFolded ? (
            <div className="niuu:flex niuu:h-full niuu:flex-col niuu:overflow-hidden">
              <div className="niuu:flex niuu:items-center niuu:justify-center niuu:border-b niuu:border-border-subtle niuu:py-2.5">
                <button
                  type="button"
                  onClick={expandList}
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
                  selectedId={listSelectedId}
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
                    selectedId={listSelectedId}
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
                              onClick={() => {
                                setSidebarMode(mode);
                                // Folders exist only when grouped by project; their home goes with them.
                                setOpenProjectId(null);
                              }}
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
                  {voiceSearch.supported && (
                    <button
                      type="button"
                      className="forge-session-voice"
                      aria-pressed={voiceSearch.listening}
                      aria-label={voiceSearch.listening ? 'Stop voice search' : 'Search by voice'}
                      title={voiceSearch.listening ? 'Stop voice search' : 'Search by voice'}
                      onClick={voiceSearch.toggle}
                      data-testid="pod-search-voice"
                    >
                      <AudioLines size={13} aria-hidden="true" />
                    </button>
                  )}
                </div>
                {voiceSearch.error && (
                  <p className="forge-session-voice-error" role="alert">
                    {voiceSearch.error}
                  </p>
                )}
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
                  selectedId={listSelectedId}
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
                {filter === 'unread' && (
                  <div className="forge-unread-list" data-testid="unread-list">
                    <p className="forge-unread-summary" data-testid="unread-list-summary">
                      {unreadSessions.length === 0
                        ? 'Nothing unread'
                        : `${unreadSessions.length} ${unreadSessions.length === 1 ? 'session has' : 'sessions have'} new replies`}
                    </p>
                    {unreadSessions.map((session) => (
                      <PodEntry
                        key={session.id}
                        session={session}
                        unreadNote
                        selected={session.id === requestedSessionId}
                        onSelect={() => handleSelectSession(session.id)}
                        showDetails={details === '1'}
                        onAction={(target, action) => void handleRowAction(target, action)}
                        onReadChange={handleReadChange}
                        readBusy={sessionRead.pendingIds.has(session.id)}
                        busy={!!rowBusyId}
                      />
                    ))}
                  </div>
                )}
                {filter !== 'unread' &&
                  sidebarGroups.map((g) => (
                    <PodGroup
                      key={g.id ?? g.label}
                      label={g.label}
                      groupKey={g.id ?? `${sidebarMode}:${g.label}`}
                      hierarchical
                      folder={
                        sidebarMode === 'project' && g.id ? projectFolders.get(g.id) : undefined
                      }
                      folderSelected={!!g.id && g.id === openProjectId}
                      onOpenFolder={g.id ? () => handleOpenProject(g.id!) : undefined}
                      sectionLabel={sidebarMode === 'project' && !g.id}
                      openRoom={openRoom}
                      contextIds={nesting.contextIds}
                      workerCounts={nesting.workerCounts}
                      sessions={g.sessions}
                      selectedId={listSelectedId}
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
                <div className="forge-session-footer-line">
                  <p className="forge-session-today" data-testid="sessions-today">
                    {workToday
                      ? workToday.sessions === 0
                        ? 'Last 24 h · no work recorded'
                        : `Last 24 h · ${workToday.sessions} ${workToday.sessions === 1 ? 'session' : 'sessions'} · ${workedDuration(workToday.seconds)}`
                      : `${listedSessionCount} ${listedSessionCount === 1 ? 'session' : 'sessions'}`}
                  </p>
                  <button
                    type="button"
                    className="forge-session-refresh"
                    aria-label="Refresh sessions"
                    title="Refresh sessions"
                    disabled={sessionsQuery.isFetching}
                    onClick={() => void sessionsQuery.refetch()}
                    data-testid="sessions-refresh"
                  >
                    <RefreshCw size={13} aria-hidden="true" />
                  </button>
                </div>
              </footer>
            </div>
          )}
        </nav>

        {/* ── Main content: session detail ───────────────────────── */}
        {!listFolded && (
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
              setOpenProjectId(null);
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
          {sessionsQuery.data && openProjectId !== null && (
            <ProjectHome
              key={openProjectId}
              projectId={openProjectId}
              name={openProjectGroup?.label ?? openProjectRecord?.name ?? openProjectId}
              project={openProjectRecord}
              sessions={allSessions}
              workerCounts={nesting.workerCounts}
              onOpenSession={handleSelectSession}
              onNewSession={() => setLaunchOpen(true)}
              onArchived={() => setOpenProjectId(null)}
            />
          )}
          {sessionsQuery.data && showUnreadStream && (
            <UnreadStream sessions={unreadSessions} onOpen={handleSelectSession} />
          )}
          {sessionsQuery.data &&
            !showUnreadStream &&
            !showProjectHome &&
            !resolvedSelectedSessionId &&
            requestedSessionId &&
            connectionStates.some((source) => source.loading) && (
              <LoadingState label="Loading selected session…" />
            )}
          {sessionsQuery.data &&
            !showUnreadStream &&
            !showProjectHome &&
            !resolvedSelectedSessionId &&
            !(requestedSessionId && connectionStates.some((source) => source.loading)) && (
              <EmptyState
                title="No session selected"
                description="Select a session from the sidebar."
              />
            )}
          {sessionsQuery.data &&
            !showUnreadStream &&
            !showProjectHome &&
            resolvedSelectedSessionId && (
              <LiveSessionDetailPage
                key={resolvedSelectedSessionId}
                sessionId={resolvedSelectedSessionId}
                focusedWorkerId={focusedPeerId}
                onFocusWorker={handleFocusWorker}
                onRoomWorkers={handleRoomWorkers}
                workersInList
                onDockOpenChange={setDockOpen}
                onBackToUnread={
                  filter === 'unread'
                    ? () => {
                        setSelectedSessionId(null);
                        void navigate({ to: '/volundr/sessions' });
                      }
                    : undefined
                }
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
