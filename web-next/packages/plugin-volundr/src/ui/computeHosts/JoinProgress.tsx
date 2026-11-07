import { Chip } from '@niuulabs/ui';
import {
  HOST_LEAVE_COMMAND,
  joinSteps,
  needsAttention,
  type ComputeHost,
} from '../../domain/computeHosts';
import { Timeline } from './Timeline';

export interface JoinProgressProps {
  host: ComputeHost;
  poolId: string;
  checking: boolean;
  onCheck: () => void;
}

/** The host that ran the command, and where Forge is with it. */
export function JoinProgress({ host, poolId, checking, onCheck }: JoinProgressProps) {
  const steps = joinSteps(host);
  return (
    <div className="niuu:grid niuu:gap-4 niuu:md:grid-cols-2">
      <div className="niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-4">
        <p className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-1.5 niuu:text-base niuu:font-semibold niuu:text-text-primary">
          {host.name}
          {host.tags.map((tag) => (
            <Chip key={tag}>{tag}</Chip>
          ))}
        </p>
        <p className="niuu:mb-3 niuu:font-mono niuu:text-xs niuu:text-text-secondary">
          {host.connection.target.replace(/:22$/, '') || host.connection.kind} · key{' '}
          {host.hostKeyFingerprint}
        </p>
        <Timeline steps={steps} />
        {host.findings.length > 0 ? (
          <ul
            role="alert"
            className="niuu:mt-2 niuu:list-disc niuu:rounded-lg niuu:border niuu:border-critical-bo niuu:bg-critical-bg niuu:py-2 niuu:pl-6 niuu:pr-3 niuu:text-xs niuu:text-critical-fg"
          >
            {host.findings.map((finding) => (
              <li key={finding}>{finding}</li>
            ))}
          </ul>
        ) : null}
        {needsAttention(host) ? (
          <button
            type="button"
            disabled={checking}
            className="niuu:mt-2 niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-tertiary niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:text-text-primary niuu:disabled:opacity-50"
            onClick={onCheck}
          >
            Check now
          </button>
        ) : null}
      </div>
      <div className="niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-4">
        <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
          On the host, the command printed
        </h3>
        <pre className="niuu:my-2 niuu:overflow-x-auto niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-3 niuu:font-mono niuu:text-xs niuu:text-text-primary">
          {`✓ registered as ${host.name} with ${poolId}\nForge is preparing this host; follow it in the UI.\nTo undo: ${HOST_LEAVE_COMMAND}`}
        </pre>
        <p className="niuu:text-xs niuu:text-text-muted">
          That&apos;s the only thing that runs by hand. Everything else (Nix, Podman, the slot
          supervisor) comes from the Forge&apos;s recipe, with the same version and digest as every
          other host.
        </p>
      </div>
    </div>
  );
}
