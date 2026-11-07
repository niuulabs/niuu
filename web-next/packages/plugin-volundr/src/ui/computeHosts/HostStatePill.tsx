import type { ComputeHost, HostTone } from '../../domain/computeHosts';
import { hostStateView } from '../../domain/computeHosts';

const TONE_CLASS: Record<HostTone, string> = {
  ok: 'niuu:bg-state-ok-bg niuu:text-state-ok',
  brand: 'niuu:bg-brand/15 niuu:text-brand',
  warn: 'niuu:bg-state-warn-bg niuu:text-state-warn',
  crit: 'niuu:bg-critical-bg niuu:text-critical-fg',
  neutral: 'niuu:bg-bg-tertiary niuu:text-text-secondary',
};

/** The host's state as a coloured pill: red only for a failed check or a lost connection. */
export function HostStatePill({ host }: { host: Pick<ComputeHost, 'state' | 'connection'> }) {
  const { label, tone } = hostStateView(host);
  return (
    <span
      data-tone={tone}
      className={`niuu:inline-flex niuu:items-center niuu:gap-1.5 niuu:whitespace-nowrap niuu:rounded-full niuu:px-2 niuu:py-0.5 niuu:text-xs ${TONE_CLASS[tone]}`}
    >
      <span
        aria-hidden="true"
        className="niuu:h-1.5 niuu:w-1.5 niuu:rounded-full niuu:bg-current"
      />
      {label}
    </span>
  );
}
