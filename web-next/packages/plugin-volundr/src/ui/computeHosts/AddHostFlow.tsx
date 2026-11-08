import { useState, type ReactNode } from 'react';
import {
  hostForCode,
  hostTagOptions,
  needsCapacity,
  type ComputeHost,
  type ComputeHostsOverview,
  type ComputeRuntime,
  type NewPairingCode,
  type PairingCodeRequest,
} from '../../domain/computeHosts';
import { errorText } from '../errorText';
import { JoinProgress } from './JoinProgress';
import { PairingCommand } from './PairingCommand';
import { RadioCard } from './RadioCard';
import { CapacityExplainer, SetCapacity } from './SetCapacity';
import { TagChipInput } from './TagChipInput';
import { useComputeHostActions } from './useComputeHosts';

const CONNECTOR_HINTS: Record<string, string> = {
  direct_ssh: 'Forge can reach the host on your network.',
  reverse_ssh: 'Behind NAT or a firewall: the host keeps one outbound SSH tunnel open to Forge.',
};

/** A runtime that runs one session per host fixes the slot count at one. */
const EXCLUSIVE_SLOTS = 1;

interface Issued {
  code: NewPairingCode;
  request: PairingCodeRequest;
  /** Hosts that existed when the code was minted: none of them is the one that joins. */
  known: ReadonlySet<string>;
}

const PANEL = 'niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-4';

/** The host being followed: the one opened from the list, else the one that ran the command. */
function followedHost(
  overview: ComputeHostsOverview,
  followHostId: string | undefined,
  issued: Issued | null,
): ComputeHost | undefined {
  if (followHostId) return overview.hosts.find((host) => host.id === followHostId);
  if (!issued) return undefined;
  return hostForCode(overview.hosts, issued.known, issued.request);
}

/** A refused runtime says why; one that fits only exclusive hosts says so. */
function runtimeNote(runtime: ComputeRuntime): string | undefined {
  if (!runtime.allowed) return runtime.refusal ?? 'Not available on registered hosts.';
  if (runtime.exclusiveOnly) return 'Exclusive hosts only.';
  return undefined;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <fieldset className="niuu:mb-4 niuu:min-w-0 niuu:border-0 niuu:p-0">
      <legend className="niuu:mb-1.5 niuu:text-sm niuu:font-medium niuu:text-text-primary">
        {label}
      </legend>
      {children}
      {hint ? <div className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">{hint}</div> : null}
    </fieldset>
  );
}

export interface AddHostFlowProps {
  instanceId: string;
  overview: ComputeHostsOverview;
  /** Back to the host list. */
  onClose: () => void;
  /** Follow an already listed host that is still joining or waits for its capacity. */
  followHostId?: string;
}

/**
 * Add host, end to end: choose what the host runs and how Forge reaches it, mint the
 * command, then follow the host that runs it until it is ready. A healthy host with no slot
 * count asks for one, with the facts and the arithmetic that suggest it.
 */
export function AddHostFlow({ instanceId, overview, onClose, followHostId }: AddHostFlowProps) {
  const actions = useComputeHostActions(instanceId);
  const firstAllowed = overview.runtimes.find((runtime) => runtime.allowed);
  const [execution, setExecution] = useState(firstAllowed?.execution ?? '');
  const [connector, setConnector] = useState(overview.connectors[0]?.bindingId ?? '');
  const [tags, setTags] = useState<string[]>([]);
  const [slotsNow, setSlotsNow] = useState(false);
  const [slotsText, setSlotsText] = useState('');
  const [issued, setIssued] = useState<Issued | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const runtime = overview.runtimes.find((item) => item.execution === execution);
  const exclusive = runtime?.exclusiveOnly ?? false;
  const requestedSlots = /^\d+$/.test(slotsText.trim()) ? Number(slotsText) : null;
  const slotsValid = exclusive || !slotsNow || (requestedSlots !== null && requestedSlots >= 1);
  const canCreate = Boolean(runtime?.allowed && connector) && slotsValid;

  const create = async () => {
    setFailure(null);
    let slots: number | null = null;
    if (exclusive) slots = EXCLUSIVE_SLOTS;
    else if (slotsNow) slots = requestedSlots;
    const request: PairingCodeRequest = { execution, connector, tags, slots };
    try {
      const code = await actions.createCode.mutateAsync(request);
      setIssued({ code, request, known: new Set(overview.hosts.map((host) => host.id)) });
    } catch (error) {
      setFailure(errorText(error));
    }
  };

  const revoke = async () => {
    if (!issued) return;
    setFailure(null);
    try {
      await actions.revokeCode.mutateAsync(issued.code.id);
      setIssued(null);
    } catch (error) {
      setFailure(errorText(error));
    }
  };

  // The command is consumed when the host runs it, so the host is found by what is new and
  // matches the request, not by the code.
  const joined = followedHost(overview, followHostId, issued);

  if (joined && needsCapacity(joined)) {
    return (
      <div className="niuu:grid niuu:gap-4 niuu:md:grid-cols-2">
        <div className={PANEL}>
          <SetCapacity
            host={joined}
            onSave={(slots) =>
              actions.update.mutateAsync({ hostId: joined.id, changes: { slots } })
            }
            onSkip={onClose}
          />
        </div>
        <div className={PANEL}>
          <CapacityExplainer host={joined} />
        </div>
      </div>
    );
  }

  if (joined) {
    return (
      <JoinProgress
        host={joined}
        poolId={overview.poolId}
        checking={actions.check.isPending}
        onCheck={() => actions.check.mutate(joined.id)}
      />
    );
  }

  const locked = issued !== null;
  return (
    <div className="niuu:grid niuu:gap-4 niuu:md:grid-cols-2">
      <div className={PANEL}>
        <Field label="Runs as">
          <div className="niuu:grid niuu:gap-1.5" role="radiogroup" aria-label="Runs as">
            {overview.runtimes.map((item) => (
              <RadioCard
                key={item.execution}
                name="runtime"
                value={item.execution}
                title={item.label}
                checked={item.execution === execution}
                disabled={locked || !item.allowed}
                onChange={() => setExecution(item.execution)}
              >
                {runtimeNote(item)}
              </RadioCard>
            ))}
          </div>
        </Field>
        <Field
          label="Tags"
          hint="Sessions and workflows that target a tag only land on hosts with it. Change anytime."
        >
          <TagChipInput
            label="Host tags"
            value={tags}
            onChange={setTags}
            suggestions={hostTagOptions(overview.hosts)}
            placeholder="add tag"
            disabled={locked}
          />
        </Field>
        <Field label="How Forge reaches it">
          <div
            className="niuu:grid niuu:gap-1.5"
            role="radiogroup"
            aria-label="How Forge reaches it"
          >
            {overview.connectors.map((item) => (
              <RadioCard
                key={item.bindingId}
                name="connector"
                value={item.bindingId}
                title={item.label}
                checked={item.bindingId === connector}
                disabled={locked}
                onChange={() => setConnector(item.bindingId)}
              >
                {CONNECTOR_HINTS[item.kind]}
              </RadioCard>
            ))}
          </div>
        </Field>
        <Field
          label="Sessions at once"
          hint={
            exclusive ? 'This runtime runs one session per host.' : '1 makes it an exclusive host.'
          }
        >
          <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2.5">
            <RadioCard
              name="slots"
              value="later"
              title="After the first check"
              checked={!slotsNow && !exclusive}
              disabled={locked || exclusive}
              onChange={() => setSlotsNow(false)}
            />
            <RadioCard
              name="slots"
              value="now"
              title="Now"
              checked={slotsNow || exclusive}
              disabled={locked || exclusive}
              onChange={() => setSlotsNow(true)}
            />
            <input
              inputMode="numeric"
              aria-label="Slots"
              placeholder="slots"
              value={exclusive ? String(EXCLUSIVE_SLOTS) : slotsText}
              disabled={locked || exclusive || !slotsNow}
              onChange={(event) => setSlotsText(event.target.value)}
              className="niuu:w-24 niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-2.5 niuu:py-1.5 niuu:text-sm niuu:text-text-primary niuu:disabled:opacity-50"
            />
          </div>
        </Field>
        {failure && !issued ? (
          <p role="alert" className="niuu:mb-3 niuu:text-sm niuu:text-critical-fg">
            {failure}
          </p>
        ) : null}
        {locked ? null : (
          <button
            type="button"
            disabled={!canCreate || actions.createCode.isPending}
            className="niuu:rounded-md niuu:bg-brand niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:font-semibold niuu:text-bg-primary niuu:disabled:opacity-50"
            onClick={() => void create()}
          >
            Create command
          </button>
        )}
      </div>
      <div className={PANEL}>
        {issued ? (
          <PairingCommand
            code={issued.code}
            revoking={actions.revokeCode.isPending}
            error={failure}
            onRevoke={() => void revoke()}
            onRestart={() => setIssued(null)}
          />
        ) : (
          <p className="niuu:text-sm niuu:text-text-muted">
            Choose what the host runs and how Forge reaches it, then create the command to run on
            the host.
          </p>
        )}
      </div>
    </div>
  );
}
