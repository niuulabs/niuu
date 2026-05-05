import { useState } from 'react';
import { StateDot, cn } from '@niuulabs/ui';
import type { AuditEntryKind } from '../../ports';
import { useAuditLog } from './useAuditLog';

const KIND_LABELS: Record<AuditEntryKind, string> = {
  'settings.flock_config.updated': 'Flock config updated',
  'settings.dispatch_defaults.updated': 'Dispatch defaults updated',
  'settings.notifications.updated': 'Notifications updated',
  'dispatcher.started': 'Dispatcher started',
  'dispatcher.stopped': 'Dispatcher stopped',
  'dispatcher.threshold_changed': 'Threshold changed',
  'dispatcher.batch_size_changed': 'Batch size changed',
  'raid.dispatched': 'Raid dispatched',
  'raid.merged': 'Raid merged',
  'raid.failed': 'Raid failed',
  'raid.escalated': 'Raid escalated',
  'saga.created': 'Saga created',
  'saga.completed': 'Saga completed',
};

const KIND_GROUPS: { label: string; kinds: AuditEntryKind[] }[] = [
  {
    label: 'Settings',
    kinds: [
      'settings.flock_config.updated',
      'settings.dispatch_defaults.updated',
      'settings.notifications.updated',
    ],
  },
  {
    label: 'Dispatcher',
    kinds: [
      'dispatcher.started',
      'dispatcher.stopped',
      'dispatcher.threshold_changed',
      'dispatcher.batch_size_changed',
    ],
  },
  {
    label: 'Raids',
    kinds: ['raid.dispatched', 'raid.merged', 'raid.failed', 'raid.escalated'],
  },
  {
    label: 'Sagas',
    kinds: ['saga.created', 'saga.completed'],
  },
];

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function kindBadgeClass(kind: AuditEntryKind): string {
  if (kind === 'raid.failed' || kind === 'raid.escalated') {
    return 'niuu-text-critical niuu-bg-critical/10';
  }
  if (kind === 'raid.merged' || kind === 'saga.completed') {
    return 'niuu-text-accent-emerald niuu-bg-accent-emerald/10';
  }
  if (kind === 'dispatcher.started' || kind === 'raid.dispatched') {
    return 'niuu-text-accent-cyan niuu-bg-accent-cyan/10';
  }
  return 'niuu-text-text-secondary niuu-bg-bg-elevated';
}

const ACTOR_COLORS: Record<string, string> = {
  system: 'niuu-text-text-muted',
  dispatcher: 'niuu-text-accent-purple',
};

function actorClass(actor: string): string {
  return ACTOR_COLORS[actor] ?? 'niuu-text-text-secondary';
}

export function AuditLogSection() {
  const [activeKinds, setActiveKinds] = useState<AuditEntryKind[]>([]);

  const filter = activeKinds.length > 0 ? { kinds: activeKinds, limit: 100 } : { limit: 100 };
  const { data: entries, isLoading, isError, error } = useAuditLog(filter);

  function toggleKind(kind: AuditEntryKind) {
    setActiveKinds((prev) =>
      prev.includes(kind) ? prev.filter((k) => k !== kind) : [...prev, kind],
    );
  }

  function clearFilters() {
    setActiveKinds([]);
  }

  return (
    <section aria-label="Audit log">
      <h3 className="niuu-text-base niuu-font-semibold niuu-text-text-primary niuu-mb-1">
        Audit Log
      </h3>
      <p className="niuu-text-sm niuu-text-text-secondary niuu-mb-4">
        Immutable record of settings changes and dispatcher events.
      </p>

      {/* Kind filters */}
      <div className="niuu-mb-4" role="group" aria-label="Filter by event type">
        <div className="niuu-flex niuu-flex-wrap niuu-gap-4">
          {KIND_GROUPS.map((group) => (
            <div key={group.label}>
              <p className="niuu-text-xs niuu-text-text-muted niuu-mb-1 niuu-uppercase niuu-tracking-wide">
                {group.label}
              </p>
              <div className="niuu-flex niuu-flex-wrap niuu-gap-1">
                {group.kinds.map((kind) => (
                  <button
                    key={kind}
                    type="button"
                    aria-pressed={activeKinds.includes(kind)}
                    onClick={() => toggleKind(kind)}
                    className={cn(
                      'niuu-px-2 niuu-py-0.5 niuu-rounded-full niuu-text-xs niuu-transition-colors niuu-border',
                      activeKinds.includes(kind)
                        ? 'niuu-border-brand niuu-bg-brand/10 niuu-text-brand'
                        : 'niuu-border-border niuu-text-text-secondary hover:niuu-border-border-subtle',
                    )}
                  >
                    {KIND_LABELS[kind]}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>

        {activeKinds.length > 0 && (
          <button
            type="button"
            onClick={clearFilters}
            className="niuu-mt-2 niuu-text-xs niuu-text-text-secondary hover:niuu-text-text-primary niuu-transition-colors"
          >
            Clear filters ({activeKinds.length} active)
          </button>
        )}
      </div>

      {/* Log table */}
      <div
        className="niuu-border niuu-border-border niuu-rounded-md niuu-overflow-hidden"
        role="log"
        aria-label="Audit log entries"
        aria-live="polite"
      >
        {isLoading && (
          <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-p-4" role="status">
            <StateDot state="processing" pulse />
            <span className="niuu-text-sm niuu-text-text-secondary">loading audit log…</span>
          </div>
        )}

        {isError && (
          <div className="niuu-flex niuu-items-center niuu-gap-2 niuu-p-4" role="alert">
            <StateDot state="failed" />
            <span className="niuu-text-sm niuu-text-critical">
              {error instanceof Error ? error.message : 'failed to load'}
            </span>
          </div>
        )}

        {entries?.length === 0 && !isLoading && (
          <p className="niuu-text-sm niuu-text-text-muted niuu-p-4">
            No entries match the current filter.
          </p>
        )}

        {entries && entries.length > 0 && (
          <table className="niuu-w-full niuu-text-sm niuu-border-collapse">
            <thead>
              <tr className="niuu-border-b niuu-border-border niuu-bg-bg-secondary">
                <th className="niuu-text-left niuu-px-3 niuu-py-2 niuu-text-xs niuu-text-text-muted niuu-font-medium niuu-w-40">
                  Time
                </th>
                <th className="niuu-text-left niuu-px-3 niuu-py-2 niuu-text-xs niuu-text-text-muted niuu-font-medium niuu-w-40">
                  Event
                </th>
                <th className="niuu-text-left niuu-px-3 niuu-py-2 niuu-text-xs niuu-text-text-muted niuu-font-medium">
                  Summary
                </th>
                <th className="niuu-text-left niuu-px-3 niuu-py-2 niuu-text-xs niuu-text-text-muted niuu-font-medium niuu-w-24">
                  Actor
                </th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr
                  key={entry.id}
                  className="niuu-border-b niuu-border-border-subtle hover:niuu-bg-bg-secondary niuu-transition-colors"
                >
                  <td className="niuu-px-3 niuu-py-2 niuu-font-mono niuu-text-xs niuu-text-text-muted niuu-whitespace-nowrap">
                    {formatDate(entry.createdAt)}
                  </td>
                  <td className="niuu-px-3 niuu-py-2">
                    <span
                      className={`niuu-px-1.5 niuu-py-0.5 niuu-rounded niuu-text-xs niuu-font-mono ${kindBadgeClass(entry.kind)}`}
                    >
                      {entry.kind}
                    </span>
                  </td>
                  <td className="niuu-px-3 niuu-py-2 niuu-text-text-primary">{entry.summary}</td>
                  <td
                    className={`niuu-px-3 niuu-py-2 niuu-font-mono niuu-text-xs ${actorClass(entry.actor)}`}
                  >
                    {entry.actor}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {entries && (
        <p className="niuu-text-xs niuu-text-text-muted niuu-mt-2">
          {entries.length} entr{entries.length !== 1 ? 'ies' : 'y'}
          {activeKinds.length > 0 ? ' (filtered)' : ''}
        </p>
      )}
    </section>
  );
}
