import { cn } from '../../utils/cn';
import { StateDot, type DotState } from '../../primitives/StateDot';
import './LifecycleBadge.css';

export type LifecycleState =
  | 'provisioning'
  | 'ready'
  | 'running'
  | 'idle'
  | 'terminating'
  | 'terminated'
  | 'failed';

interface LifecycleMeta {
  dotState: DotState;
  pulse: boolean;
}

/**
 * Maps each lifecycle state to a dot state + pulse flag.
 * Matches the Völundr design spec:
 * - provisioning: queued, pulsing
 * - ready: healthy
 * - running: running, pulsing
 * - idle: idle
 * - terminating: attention, pulsing
 * - terminated: archived
 * - failed: failed
 */
export const LIFECYCLE_META: Record<LifecycleState, LifecycleMeta> = {
  provisioning: { dotState: 'queued', pulse: true },
  ready: { dotState: 'healthy', pulse: false },
  running: { dotState: 'running', pulse: true },
  idle: { dotState: 'idle', pulse: false },
  terminating: { dotState: 'attention', pulse: true },
  terminated: { dotState: 'archived', pulse: false },
  failed: { dotState: 'failed', pulse: false },
};

export interface LifecycleBadgeProps {
  state: LifecycleState;
  className?: string;
}

/**
 * Lifecycle badge — state pill with a colored dot for Völundr sessions.
 *
 * States: provisioning / ready / running / idle / terminating / terminated / failed
 */
export function LifecycleBadge({ state, className }: LifecycleBadgeProps) {
  const meta = LIFECYCLE_META[state];

  return (
    <span
      className={cn('niuu-lifecycle-badge', `niuu-lifecycle-badge--${state}`, className)}
      aria-label={state}
    >
      <StateDot state={meta.dotState} pulse={meta.pulse} size={6} />
      <span>{state}</span>
    </span>
  );
}
