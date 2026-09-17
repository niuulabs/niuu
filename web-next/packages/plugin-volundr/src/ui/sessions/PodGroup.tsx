import { StateDot, relTime, cn } from '@niuulabs/ui';
import { Archive, Check, ChevronRight, Square, SquareTerminal, Ticket, Trash2 } from 'lucide-react';
import { useShowDebugMeta } from '../uxPrefs';
import type { Session } from '../../domain/session';
import {
  SESSION_DOT,
  STOPPABLE_STATES,
  compactSourceParts,
  looksLikeRepoLabel,
  sessionOriginBadge,
  toGroupTestId,
} from './sessionLabels';
import '../session-card.css';

// ---------------------------------------------------------------------------
// PodEntry — a single session row in the sidebar
// ---------------------------------------------------------------------------

function PodEntry({
  session,
  selected,
  onSelect,
  onStop,
  onArchive,
  onDelete,
  busy = false,
  collapsed = false,
  selectable = false,
  checked = false,
  onToggleSelection,
  index = 0,
}: {
  session: Session;
  selected: boolean;
  onSelect: () => void;
  /** Stop the session in place (hover action). */
  onStop?: (id: string) => void;
  /** Stop (if running) then archive the session (hover action). */
  onArchive?: (id: string) => void;
  onDelete?: (id: string) => void;
  /** True while this row has an action in flight — disables its buttons. */
  busy?: boolean;
  collapsed?: boolean;
  selectable?: boolean;
  checked?: boolean;
  onToggleSelection?: () => void;
  /** Row position within its group — drives the zebra striping. */
  index?: number;
}) {
  const canStop = STOPPABLE_STATES.includes(session.state) || session.state === 'awaiting_input';
  const canArchive = session.state !== 'archived';
  const lastActiveMs = new Date(session.lastActivityAt ?? session.startedAt).getTime();
  const ageLabel = relTime(lastActiveMs);
  const isActivelyWorking = session.state === 'running' || session.state === 'awaiting_input';
  const primaryLabel = session.name || session.personaName || '(unnamed)';
  // saga/run/ravn id and forge/cluster id are platform plumbing. ravnId in
  // particular falls back to the owner id ("dev-user") when there is no real
  // tracker, so it renders as a meaningless "ticket". Hide both unless the
  // operator opts into debug metadata. See uxPrefs.getShowDebugMeta.
  const showDebugMeta = useShowDebugMeta();
  const trackerLabel = showDebugMeta
    ? (session.sagaId ?? session.runId ?? session.ravnId)
    : undefined;
  const previewLabel = session.preview;
  const sourceParts =
    previewLabel && looksLikeRepoLabel(previewLabel) ? compactSourceParts(previewLabel) : null;
  const showPreviewFallback = previewLabel && !sourceParts;
  const originBadge = sessionOriginBadge(session);
  const forgeLabel = showDebugMeta ? (session.clusterName ?? session.clusterId) : undefined;
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect();
        }
      }}
      data-testid={`pod-entry-${session.id}`}
      aria-pressed={selected}
      data-zebra={index % 2 === 1 || undefined}
      className={cn(
        'lx-pod-entry niuu:flex niuu:w-full niuu:items-start niuu:gap-2 niuu:border-b niuu:border-l-2 niuu:px-3 niuu:py-1.5 niuu:text-left niuu:transition-colors',
        selected
          ? 'lx-pod-entry--selected niuu:border-brand niuu:border-b-white/10'
          : 'niuu:border-transparent niuu:border-b-white/6',
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
      <StateDot state={SESSION_DOT[session.state]} pulse={isActivelyWorking} />
      {collapsed ? null : (
        <>
          <div className="niuu:flex-1 niuu:min-w-0 niuu:flex niuu:flex-col niuu:gap-0.5">
            <div className="niuu:flex niuu:min-w-0 niuu:items-baseline niuu:gap-2">
              <span className="niuu:flex-1 niuu:min-w-0 niuu:font-mono niuu:text-[13px] niuu:font-medium niuu:text-text-primary niuu:truncate">
                {primaryLabel}
              </span>
              <span className="lx-pod-age niuu:flex-shrink-0 niuu:font-mono niuu:text-[10px] niuu:text-text-secondary">
                {ageLabel}
              </span>
            </div>
            <div className="niuu:flex niuu:min-w-0 niuu:flex-wrap niuu:items-center niuu:gap-x-2 niuu:gap-y-0.5 niuu:font-mono niuu:text-[10px] niuu:text-text-muted">
              {trackerLabel ? (
                <span
                  className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-1.5"
                  title={trackerLabel}
                >
                  <Ticket className="niuu:h-3 niuu:w-3 niuu:flex-shrink-0 niuu:text-text-faint" />
                  <span className="niuu:truncate niuu:text-brand">{trackerLabel}</span>
                </span>
              ) : null}
              {forgeLabel ? (
                <span
                  className="niuu:inline-flex niuu:min-w-0 niuu:items-center niuu:rounded-full niuu:border niuu:border-brand/20 niuu:bg-brand/10 niuu:px-2 niuu:py-0.5"
                  title={forgeLabel}
                >
                  <span className="niuu:truncate niuu:text-brand">{forgeLabel}</span>
                </span>
              ) : null}
              {originBadge ? (
                <span
                  className="niuu:inline-flex niuu:flex-shrink-0 niuu:items-center niuu:rounded-full niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5"
                  title={`Imported from ${originBadge}`}
                  data-testid={`session-origin-badge-${session.id}`}
                >
                  <span className="niuu:text-[9px] niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-secondary">
                    {originBadge}
                  </span>
                </span>
              ) : null}
              {sourceParts ? (
                <span
                  className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-1.5 niuu:text-text-secondary"
                  title={previewLabel}
                >
                  <span className="niuu:truncate">{sourceParts.label}</span>
                  {sourceParts.branch ? (
                    <span className="niuu:flex-shrink-0 niuu:text-brand">
                      @{sourceParts.branch}
                    </span>
                  ) : null}
                </span>
              ) : null}
              {showPreviewFallback ? (
                <span
                  className="niuu:flex niuu:min-w-0 niuu:items-center niuu:gap-1.5"
                  title={previewLabel}
                >
                  <SquareTerminal className="niuu:h-3 niuu:w-3 niuu:flex-shrink-0 niuu:text-text-faint" />
                  <span className="niuu:truncate">{previewLabel}</span>
                </span>
              ) : null}
            </div>
          </div>
          {(canStop && onStop) || (canArchive && onArchive) || onDelete ? (
            <div className="lx-pod-actions" onClick={(e) => e.stopPropagation()}>
              {canStop && onStop ? (
                <button
                  type="button"
                  className="lx-pod-action-btn lx-pod-action-btn--stop"
                  title="Stop session"
                  aria-label={`Stop session ${primaryLabel}`}
                  data-testid={`pod-entry-${session.id}-stop`}
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    onStop(session.id);
                  }}
                >
                  <Square size={11} fill="currentColor" />
                </button>
              ) : null}
              {canArchive && onArchive ? (
                <button
                  type="button"
                  className="lx-pod-action-btn lx-pod-action-btn--archive"
                  title="Stop & archive session"
                  aria-label={`Stop and archive session ${primaryLabel}`}
                  data-testid={`pod-entry-${session.id}-archive`}
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    onArchive(session.id);
                  }}
                >
                  <Archive size={11} />
                </button>
              ) : null}
              {onDelete ? (
                <button
                  type="button"
                  className="lx-pod-action-btn lx-pod-action-btn--stop"
                  title="Delete session"
                  aria-label={`Delete session ${primaryLabel}`}
                  data-testid={`pod-entry-${session.id}-delete`}
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(session.id);
                  }}
                >
                  <Trash2 size={11} />
                </button>
              ) : null}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PodGroup — a state section with label + session entries
// ---------------------------------------------------------------------------

export function PodGroup({
  label,
  sessions,
  selectedId,
  onSelect,
  onStop,
  onArchive,
  onDelete,
  busyId,
  collapsed = false,
  selectableSessionIds,
  selectedSessionIds,
  onToggleSelection,
  folded = false,
  onToggleFold,
  hideHeader = false,
}: {
  label: string;
  sessions: Session[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onStop?: (id: string) => void;
  onArchive?: (id: string) => void;
  onDelete?: (id: string) => void;
  /** Id of the session whose row action is currently in flight. */
  busyId?: string | null;
  collapsed?: boolean;
  selectableSessionIds?: ReadonlySet<string>;
  selectedSessionIds?: ReadonlySet<string>;
  onToggleSelection?: (id: string) => void;
  /** Whether the group's session rows are folded away (header still shown). */
  folded?: boolean;
  /** Toggles the folded state. When absent, the header is not interactive. */
  onToggleFold?: () => void;
  /** Renders the rows without the group header — the caller shows the label itself. */
  hideHeader?: boolean;
}) {
  if (sessions.length === 0) return null;

  return (
    <div data-testid={`pod-group-${toGroupTestId(label)}`}>
      {!collapsed && !hideHeader && (
        <button
          type="button"
          onClick={onToggleFold}
          disabled={!onToggleFold}
          aria-expanded={!folded}
          data-testid={`pod-group-${toGroupTestId(label)}-header`}
          className={cn(
            'niuu:flex niuu:w-full niuu:items-center niuu:gap-1.5 niuu:border-b niuu:border-white/6 niuu:px-2.5 niuu:py-2 niuu:text-left niuu:text-[10px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.18em] niuu:text-text-muted niuu:transition-colors',
            onToggleFold && 'niuu:hover:text-text-primary',
          )}
        >
          <ChevronRight
            className="vol-session-group-chevron niuu:h-3 niuu:w-3 niuu:flex-shrink-0 niuu:text-text-faint niuu:transition-transform"
            data-folded={folded}
            aria-hidden="true"
          />
          <span className="niuu:flex-1 niuu:truncate">{label}</span>
          <span
            className="niuu:font-mono niuu:text-text-faint"
            data-testid={`pod-group-${toGroupTestId(label)}-count`}
          >
            {sessions.length}
          </span>
        </button>
      )}
      {!folded &&
        sessions.map((s, i) => (
          <PodEntry
            key={s.id}
            session={s}
            selected={s.id === selectedId}
            onSelect={() => onSelect(s.id)}
            onStop={onStop}
            onArchive={onArchive}
            onDelete={onDelete}
            busy={!!busyId}
            collapsed={collapsed}
            index={i}
            selectable={selectableSessionIds?.has(s.id) ?? false}
            checked={selectedSessionIds?.has(s.id) ?? false}
            onToggleSelection={
              onToggleSelection && selectableSessionIds?.has(s.id)
                ? () => onToggleSelection(s.id)
                : undefined
            }
          />
        ))}
    </div>
  );
}
