import { useState } from 'react';
import { ErrorState, LoadingState, SegmentedFilter } from '@niuulabs/ui';
import {
  COMPUTE_HOSTS_POLL_MS,
  PAIRING_POLL_MS,
  filterHosts,
  hostTagOptions,
  type ComputeHost,
  type HostAction,
  type TagMatch,
} from '../../domain/computeHosts';
import { errorText } from '../errorText';
import { useMinuteClock } from '../sessions/timeFact';
import { AddHostFlow } from './AddHostFlow';
import { HostRemovalDialog, type RemovalMode } from './HostRemovalDialog';
import { HostSummaryCards } from './HostSummaryCards';
import { HostTable } from './HostTable';
import { TagChipInput } from './TagChipInput';
import { useComputeHostActions, useComputeHosts } from './useComputeHosts';

type Screen = { kind: 'list' } | { kind: 'add'; followHostId?: string };

const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-tertiary niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:text-text-primary niuu:disabled:opacity-50';
const PRIMARY =
  'niuu:rounded-md niuu:bg-brand niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:font-semibold niuu:text-bg-primary';

const LEGEND: Array<{ label: string; swatch: string }> = [
  { label: 'in use', swatch: 'niuu:bg-brand' },
  { label: 'starting', swatch: 'niuu:bg-state-warn' },
  { label: 'free', swatch: 'niuu:bg-bg-elevated' },
  { label: 'not clean', swatch: 'niuu:bg-critical' },
];

const MATCH_OPTIONS: Array<{ value: TagMatch; label: string }> = [
  { value: 'all', label: 'all' },
  { value: 'any', label: 'any' },
];

/**
 * A Forge's compute hosts: the pool summary, the host list with its actions, and the Add
 * host flow. Renders nothing for a Forge that has no compute hosts (its list answers 404).
 */
export function ComputeHostsPanel({ instanceId }: { instanceId: string }) {
  const [screen, setScreen] = useState<Screen>({ kind: 'list' });
  const query = useComputeHosts(
    instanceId,
    screen.kind === 'add' ? PAIRING_POLL_MS : COMPUTE_HOSTS_POLL_MS,
  );
  const actions = useComputeHostActions(instanceId);
  const now = useMinuteClock();
  const [tags, setTags] = useState<string[]>([]);
  const [match, setMatch] = useState<TagMatch>('all');
  const [removal, setRemoval] = useState<{ host: ComputeHost; mode: RemovalMode } | null>(null);
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());
  const [failure, setFailure] = useState<string | null>(null);

  if (query.isLoading) return <LoadingState label="Loading hosts…" />;
  if (query.error) {
    return (
      <ErrorState
        title="Failed to load hosts"
        message={errorText(query.error)}
        action={
          <button type="button" className={BUTTON} onClick={() => void query.refetch()}>
            Retry
          </button>
        }
      />
    );
  }
  const overview = query.data;
  if (!overview) return null;

  const holding = async (hostId: string, work: () => Promise<unknown>) => {
    setFailure(null);
    setBusy((current) => new Set(current).add(hostId));
    try {
      await work();
    } catch (error) {
      setFailure(errorText(error));
    }
    setBusy((current) => {
      const next = new Set(current);
      next.delete(hostId);
      return next;
    });
  };

  const onAction = (host: ComputeHost, action: HostAction) => {
    if (action === 'open') {
      setScreen({ kind: 'add', followHostId: host.id });
      return;
    }
    if (action === 'check') {
      void holding(host.id, () => actions.check.mutateAsync(host.id));
      return;
    }
    if (action === 'resume' || action === 'cancel_drain') {
      // Forge's resume returns a draining host to service, and an out-of-service one too.
      void holding(host.id, () => actions.resume.mutateAsync(host.id));
      return;
    }
    if (action === 'reprepare') {
      void holding(host.id, () => actions.reprepare.mutateAsync(host.id));
      return;
    }
    setRemoval({ host, mode: action });
  };

  const visible = filterHosts(overview.hosts, tags, match);

  if (screen.kind === 'add' && overview.canManage) {
    return (
      <div className="niuu:space-y-4">
        <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
          <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">Add host</h2>
          <button type="button" className={BUTTON} onClick={() => setScreen({ kind: 'list' })}>
            Back to hosts
          </button>
        </div>
        <AddHostFlow
          instanceId={instanceId}
          overview={overview}
          followHostId={screen.followHostId}
          onClose={() => setScreen({ kind: 'list' })}
        />
      </div>
    );
  }

  return (
    <div className="niuu:space-y-4">
      {overview.canManage ? null : (
        <p
          data-testid="hosts-read-only-note"
          className="niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-secondary"
        >
          You can see these hosts and your sessions can run on them. Only an admin of this Forge can
          add or change them.
        </p>
      )}
      <HostSummaryCards summary={overview.summary} />

      <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:justify-between niuu:gap-3">
        <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-3">
          <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">Hosts</h2>
          <TagChipInput
            label="Filter by tags"
            value={tags}
            onChange={setTags}
            suggestions={hostTagOptions(overview.hosts)}
          >
            <SegmentedFilter<TagMatch>
              aria-label="Tag match"
              options={MATCH_OPTIONS}
              value={match}
              onChange={setMatch}
            />
          </TagChipInput>
        </div>
        {overview.canManage ? (
          <div className="niuu:flex niuu:gap-2">
            <button
              type="button"
              className={BUTTON}
              disabled={visible.length === 0}
              onClick={() =>
                visible.forEach(
                  (host) => void holding(host.id, () => actions.check.mutateAsync(host.id)),
                )
              }
            >
              Check all
            </button>
            <button type="button" className={PRIMARY} onClick={() => setScreen({ kind: 'add' })}>
              Add host
            </button>
          </div>
        ) : null}
      </div>

      {failure ? (
        <p
          role="alert"
          className="niuu:rounded-lg niuu:border niuu:border-critical-bo niuu:bg-critical-bg niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-critical-fg"
        >
          {failure}
        </p>
      ) : null}

      <HostTable
        hosts={visible}
        now={now}
        busy={busy}
        readOnly={!overview.canManage}
        onAction={onAction}
        onTagsChange={(host, next) =>
          void holding(host.id, () =>
            actions.update.mutateAsync({ hostId: host.id, changes: { tags: next } }),
          )
        }
      />

      <div className="niuu:flex niuu:flex-wrap niuu:gap-4 niuu:text-xs niuu:text-text-muted">
        {LEGEND.map((item) => (
          <span key={item.label} className="niuu:inline-flex niuu:items-center niuu:gap-1.5">
            <i
              aria-hidden="true"
              className={`niuu:inline-block niuu:h-3 niuu:w-2 niuu:rounded-sm ${item.swatch}`}
            />
            {item.label}
          </span>
        ))}
      </div>

      {removal ? (
        <HostRemovalDialog
          host={removal.host}
          mode={removal.mode}
          onDrain={(remove) => actions.drain.mutateAsync({ hostId: removal.host.id, remove })}
          onRemove={() => actions.remove.mutateAsync(removal.host.id)}
          onClose={() => setRemoval(null)}
        />
      ) : null}
    </div>
  );
}
