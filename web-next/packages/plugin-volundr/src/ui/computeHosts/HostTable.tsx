import { Fragment, useState, type KeyboardEvent } from 'react';
import { Chip } from '@niuulabs/ui';
import {
  findingTone,
  hostActions,
  parseTagInput,
  slotCells,
  slotSummary,
  type ComputeHost,
  type HostAction,
  type SlotCell,
} from '../../domain/computeHosts';
import { connectionLabel, formatSize, hostSubline } from './hostView';
import { HostStatePill } from './HostStatePill';

const COLUMN_COUNT = 7;

const ACTION_LABEL: Record<HostAction, string> = {
  open: 'Open',
  check: 'Check now',
  drain: 'Drain',
  drain_remove: 'Drain and remove',
  cancel_drain: 'Cancel drain',
  resume: 'Resume',
  reprepare: 'Re-prepare',
  remove: 'Remove',
};

const CELL_CLASS: Record<SlotCell, string> = {
  busy: 'niuu:bg-brand',
  starting: 'niuu:bg-state-warn',
  failed: 'niuu:bg-critical',
  free: 'niuu:bg-bg-elevated',
};

const CELL_LABEL: Record<SlotCell, string> = {
  busy: 'in use',
  starting: 'starting',
  failed: 'not clean',
  free: 'free',
};

function SlotBar({ host }: { host: ComputeHost }) {
  const cells = slotCells(host);
  if (!cells.length) return null;
  return (
    <div
      role="img"
      aria-label={`Slots of ${host.name}`}
      className="niuu:flex niuu:max-w-44 niuu:flex-wrap niuu:gap-0.5"
    >
      {cells.map((cell, slot) => (
        <i
          key={slot}
          title={`slot ${slot}: ${CELL_LABEL[cell]}`}
          data-cell={cell}
          className={`niuu:block niuu:h-3.5 niuu:w-2 niuu:rounded-sm ${CELL_CLASS[cell]}`}
        />
      ))}
    </div>
  );
}

/** A host's tags as chips; the admin edits them in place. */
function HostTagEditor({
  host,
  disabled,
  onChange,
}: {
  host: ComputeHost;
  disabled: boolean;
  onChange: (tags: string[]) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState('');

  const finish = (commit: boolean) => {
    const added = commit ? parseTagInput(draft).filter((tag) => !host.tags.includes(tag)) : [];
    setDraft('');
    setAdding(false);
    if (added.length) onChange([...host.tags, ...added]);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') finish(true);
    if (event.key === 'Escape') finish(false);
  };

  return (
    <div className="niuu:group niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-1">
      {host.tags.map((tag) => (
        <Chip key={tag}>
          {tag}
          <button
            type="button"
            disabled={disabled}
            aria-label={`Remove tag ${tag} from ${host.name}`}
            className="niuu:text-text-muted niuu:opacity-0 niuu:hover:text-text-primary niuu:focus:opacity-100 niuu:group-hover:opacity-100"
            onClick={() => onChange(host.tags.filter((item) => item !== tag))}
          >
            ×
          </button>
        </Chip>
      ))}
      {adding ? (
        <input
          autoFocus
          value={draft}
          aria-label={`New tag for ${host.name}`}
          placeholder="tag"
          className="niuu:w-20 niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-1.5 niuu:py-0.5 niuu:text-xs niuu:text-text-primary niuu:outline-none"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          onBlur={() => finish(true)}
        />
      ) : (
        <button
          type="button"
          disabled={disabled}
          aria-label={`Add tag to ${host.name}`}
          className="niuu:rounded-full niuu:border niuu:border-dashed niuu:border-border niuu:px-2 niuu:py-0.5 niuu:text-xs niuu:text-text-muted niuu:hover:text-text-primary"
          onClick={() => setAdding(true)}
        >
          +
        </button>
      )}
    </div>
  );
}

function Findings({ host }: { host: ComputeHost }) {
  const tone = findingTone(host);
  const sessions = host.sessions.length;
  const toneClass =
    tone === 'crit'
      ? 'niuu:border-critical-bo niuu:bg-critical-bg niuu:text-critical-fg'
      : 'niuu:border-state-warn/35 niuu:bg-state-warn-bg niuu:text-state-warn';
  return (
    <div
      role="note"
      data-testid={`findings-${host.name}`}
      className={`niuu:rounded-lg niuu:border niuu:px-2.5 niuu:py-2 niuu:text-xs ${toneClass}`}
    >
      {host.state === 'quarantined' ? (
        <p>
          This host takes no new sessions until it passes a check. Running sessions are not touched.
        </p>
      ) : (
        <p>
          {host.connection.lastError
            ? `Forge lost the connection: ${host.connection.lastError}.`
            : 'Forge has no connection to this host.'}{' '}
          {sessions > 0
            ? `Its ${sessions} session${sessions === 1 ? ' keeps' : 's keep'} running there and show "host disconnected" until it reconnects; nothing new is placed on it.`
            : 'Nothing new is placed on it.'}
        </p>
      )}
      {host.findings.length > 0 ? (
        <ul className="niuu:mt-1 niuu:list-disc niuu:pl-4 niuu:font-mono niuu:text-text-primary">
          {host.findings.map((finding) => (
            <li key={finding}>{finding}</li>
          ))}
        </ul>
      ) : null}
      {host.state === 'quarantined' ? (
        <p className="niuu:mt-1">
          Fix it on the host, then <b>Check now</b>.
        </p>
      ) : null}
    </div>
  );
}

export interface HostTableProps {
  hosts: readonly ComputeHost[];
  now: number;
  /** Ids of hosts with a change in flight; their controls wait for it. */
  busy: ReadonlySet<string>;
  onAction: (host: ComputeHost, action: HostAction) => void;
  onTagsChange: (host: ComputeHost, tags: string[]) => void;
}

/** One row per host, with its findings inline under the rows that need attention. */
export function HostTable({ hosts, now, busy, onAction, onTagsChange }: HostTableProps) {
  return (
    <div className="niuu:overflow-x-auto niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary">
      <table className="niuu:w-full niuu:min-w-[60rem] niuu:border-collapse">
        <thead>
          <tr className="niuu:text-left niuu:text-xs niuu:text-text-muted">
            {['Host', 'Tags', 'State', 'Slots', 'Size', 'Connection'].map((heading) => (
              <th key={heading} scope="col" className="niuu:px-3 niuu:py-2 niuu:font-medium">
                {heading}
              </th>
            ))}
            <th scope="col" className="niuu:px-3 niuu:py-2 niuu:text-right niuu:font-medium">
              Actions
            </th>
          </tr>
        </thead>
        <tbody>
          {hosts.length === 0 ? (
            <tr>
              <td
                colSpan={COLUMN_COUNT}
                className="niuu:px-3 niuu:py-6 niuu:text-center niuu:text-sm niuu:text-text-muted"
              >
                No hosts match.
              </td>
            </tr>
          ) : null}
          {hosts.map((host) => {
            const pending = busy.has(host.id);
            const showFindings = host.state === 'quarantined' || host.state === 'unreachable';
            return (
              <Fragment key={host.id}>
                <tr
                  data-testid={`host-row-${host.name}`}
                  className="niuu:border-t niuu:border-border-subtle niuu:align-top"
                >
                  <td className="niuu:px-3 niuu:py-3">
                    <div className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                      {host.name}
                    </div>
                    <div className="niuu:text-xs niuu:text-text-muted">
                      {hostSubline(host, now)}
                    </div>
                  </td>
                  <td className="niuu:px-3 niuu:py-3">
                    <HostTagEditor
                      host={host}
                      disabled={pending}
                      onChange={(tags) => onTagsChange(host, tags)}
                    />
                  </td>
                  <td className="niuu:px-3 niuu:py-3">
                    <HostStatePill host={host} />
                    {host.state === 'draining' && host.removeWhenEmpty ? (
                      <div className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
                        then removed
                      </div>
                    ) : null}
                  </td>
                  <td className="niuu:px-3 niuu:py-3">
                    <SlotBar host={host} />
                    <div className="niuu:text-xs niuu:text-text-muted">{slotSummary(host)}</div>
                  </td>
                  <td className="niuu:px-3 niuu:py-3 niuu:text-sm niuu:text-text-secondary">
                    {formatSize(host.facts)}
                  </td>
                  <td className="niuu:whitespace-nowrap niuu:px-3 niuu:py-3 niuu:text-xs niuu:text-text-secondary">
                    <span
                      aria-hidden="true"
                      className={`niuu:mr-1.5 niuu:inline-block niuu:h-1.5 niuu:w-1.5 niuu:rounded-full ${
                        host.connection.connected ? 'niuu:bg-state-ok' : 'niuu:bg-critical'
                      }`}
                    />
                    {connectionLabel(host.connection, now)}
                  </td>
                  <td className="niuu:px-3 niuu:py-3">
                    <div className="niuu:flex niuu:flex-wrap niuu:justify-end niuu:gap-0.5">
                      {hostActions(host).map((action) => (
                        <button
                          key={action}
                          type="button"
                          disabled={pending}
                          aria-label={`${ACTION_LABEL[action]} ${host.name}`}
                          className="niuu:rounded-md niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-secondary niuu:hover:bg-bg-tertiary niuu:hover:text-text-primary niuu:disabled:opacity-50"
                          onClick={() => onAction(host, action)}
                        >
                          {ACTION_LABEL[action]}
                        </button>
                      ))}
                    </div>
                  </td>
                </tr>
                {showFindings ? (
                  <tr>
                    <td colSpan={COLUMN_COUNT} className="niuu:px-3 niuu:pb-3">
                      <Findings host={host} />
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
