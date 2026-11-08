import { useState } from 'react';
import { Chip } from '@niuulabs/ui';
import type { ComputeHost } from '../../domain/computeHosts';
import { errorText } from '../errorText';
import { formatMib, kernelLabel } from './hostView';

function Fact({ label, children }: { label: string; children: string }) {
  return (
    <div className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-2.5 niuu:py-2 niuu:text-sm niuu:text-text-primary">
      <div className="niuu:text-xs niuu:text-text-muted">{label}</div>
      {children}
    </div>
  );
}

export interface SetCapacityProps {
  host: ComputeHost;
  /** PATCH the slot count; rejects with the platform's reason (the arithmetic) when refused. */
  onSave: (slots: number) => Promise<unknown>;
  /** Leave the slot count unset: the host stays out of service until it is set. */
  onSkip: () => void;
}

function parseSlots(value: string): number | null {
  if (!/^\d+$/.test(value.trim())) return null;
  const slots = Number(value);
  return slots >= 1 ? slots : null;
}

/**
 * The measured facts of a prepared host, the slot count they suggest with its arithmetic,
 * and the field to set it. The platform refuses a number over what the host can hold and
 * says why; that answer is shown as given.
 */
export function SetCapacity({ host, onSave, onSkip }: SetCapacityProps) {
  const suggested = host.suggestedSlots;
  const [value, setValue] = useState(suggested ? String(suggested.slots) : '');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const slots = parseSlots(value);
  const { facts } = host;

  const save = async () => {
    if (slots === null) return;
    setPending(true);
    setError(null);
    try {
      await onSave(slots);
    } catch (failure) {
      setError(errorText(failure));
    }
    setPending(false);
  };

  return (
    <div className="niuu:space-y-3">
      <div>
        <p className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
          Prepared and audited
        </p>
        <p className="niuu:text-sm niuu:text-text-secondary">
          Recipe {host.execution} ran and the audit found nothing.
        </p>
      </div>
      <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2 niuu:sm:grid-cols-3">
        <Fact label="CPU">{facts.cpus === null ? '—' : `${facts.cpus} cores`}</Fact>
        <Fact label="Memory">{facts.memoryMib === null ? '—' : formatMib(facts.memoryMib)}</Fact>
        <Fact label="Disk free">
          {facts.diskFreeMib === null ? '—' : formatMib(facts.diskFreeMib)}
        </Fact>
        <Fact label="Kernel">{kernelLabel(facts)}</Fact>
        <Fact label="Arch">{facts.arch ?? '—'}</Fact>
        <Fact label="GPU">{facts.gpus ? String(facts.gpus) : 'none'}</Fact>
      </div>
      <div
        data-testid="slot-arithmetic"
        className="niuu:rounded-md niuu:border niuu:border-dashed niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:font-mono niuu:text-xs niuu:leading-6 niuu:text-text-secondary"
      >
        {suggested ? (
          <>
            {suggested.lines.map((line) => (
              <div key={line}>{line}</div>
            ))}
            <div>
              suggested:{' '}
              <b className="niuu:font-medium niuu:text-text-primary">
                {suggested.slots} session{suggested.slots === 1 ? '' : 's'} at once
              </b>
            </div>
          </>
        ) : (
          <div>No suggestion yet: the check has not measured this host.</div>
        )}
      </div>
      <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2.5">
        <input
          inputMode="numeric"
          aria-label="Sessions at once"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          className="niuu:w-24 niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-2.5 niuu:py-1.5 niuu:text-sm niuu:text-text-primary"
        />
        <span className="niuu:text-xs niuu:text-text-muted">1 = exclusive host</span>
      </div>
      <p className="niuu:text-xs niuu:text-text-muted">
        {suggested
          ? `More than ${suggested.slots} is refused with this arithmetic. `
          : 'A number over what the host can hold is refused with the arithmetic. '}
        Changing it later needs the host drained.
      </p>
      {error ? (
        <p role="alert" className="niuu:text-sm niuu:text-critical-fg">
          {error}
        </p>
      ) : null}
      <div className="niuu:flex niuu:gap-2">
        <button
          type="button"
          disabled={slots === null || pending}
          className="niuu:rounded-md niuu:bg-brand niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:font-semibold niuu:text-bg-primary niuu:disabled:opacity-50"
          onClick={() => void save()}
        >
          Put in service
        </button>
        <button
          type="button"
          disabled={pending}
          className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-tertiary niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:text-text-primary"
          onClick={onSkip}
        >
          Keep out of service
        </button>
      </div>
    </div>
  );
}

/**
 * What a session on the host gets, what happens when it stops, and how it is targeted. The
 * first two describe the `host` runtime (Nix and rootless Podman); another runtime's hosts
 * get only the targeting note.
 */
export function CapacityExplainer({ host }: { host: ComputeHost }) {
  const [first] = host.tags;
  const site = host.tags.find((tag) => tag !== first);
  return (
    <div className="niuu:space-y-3">
      {host.runtime === 'host' ? <HostRuntimeNotes /> : null}
      {first ? (
        <div>
          <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">Targeting</h3>
          <p className="niuu:text-sm niuu:text-text-secondary">
            A session or workflow targeting <Chip>{first}</Chip> can now land here.
            {site ? (
              <>
                {' '}
                With <Chip>{site}</Chip> it also matches the other hosts that carry it.
              </>
            ) : null}
          </p>
        </div>
      ) : null}
    </div>
  );
}

function HostRuntimeNotes() {
  return (
    <>
      <div>
        <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
          Each session on this host gets
        </h3>
        <p className="niuu:text-sm niuu:text-text-secondary">
          Its own user, network namespace (NAT out, LAN denied except allowed ranges), process
          namespace, private /tmp and a cgroup slice with the limits above. No sudo, no Docker
          group; rootless Podman for its containers.
        </p>
      </div>
      <div>
        <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
          When a session stops
        </h3>
        <p className="niuu:text-sm niuu:text-text-secondary">
          Its slot is archived and torn down, then checked: no process, file, namespace, rule or
          slice of that user may remain. Only then is the slot used again. Anything left keeps the
          slot out and shows on the host&apos;s row.
        </p>
      </div>
    </>
  );
}
