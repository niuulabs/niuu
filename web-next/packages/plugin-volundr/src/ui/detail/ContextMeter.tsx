import { CONTEXT_WARN_PERCENT, type ContextMeterData } from './sessionHeaderModel';
import './detail.css';

const RING_VIEWBOX = 20;
const RING_CENTER = RING_VIEWBOX / 2;
const RING_RADIUS = 8;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

/**
 * The header's compact meter: a ring for how full the context window is, and what the
 * session has cost. Each piece appears only when the runtime or Forge reported it.
 */
export function ContextMeter({
  context,
  title,
  cost,
}: {
  context: ContextMeterData | null;
  /** What the ring means in words ("124k of 200k context used"). */
  title?: string;
  /** The cost as text, when Forge reported one. */
  cost?: string | null;
}) {
  if (!context && !cost) return null;
  const warn = context !== null && context.percent > CONTEXT_WARN_PERCENT;
  return (
    <span className="forge-meter" data-testid="session-meter" title={title}>
      {context ? (
        <>
          <svg
            className="forge-meter__ring"
            viewBox={`0 0 ${RING_VIEWBOX} ${RING_VIEWBOX}`}
            aria-hidden="true"
            data-testid="session-context-ring"
          >
            <circle
              cx={RING_CENTER}
              cy={RING_CENTER}
              r={RING_RADIUS}
              className="forge-meter__track"
            />
            <circle
              cx={RING_CENTER}
              cy={RING_CENTER}
              r={RING_RADIUS}
              className={warn ? 'forge-meter__fill forge-meter__fill--warn' : 'forge-meter__fill'}
              strokeDasharray={`${(RING_CIRCUMFERENCE * context.percent) / 100} ${RING_CIRCUMFERENCE}`}
              transform={`rotate(-90 ${RING_CENTER} ${RING_CENTER})`}
            />
          </svg>
          <span data-testid="session-context-percent">{context.percent}%</span>
        </>
      ) : null}
      {context && cost ? <span aria-hidden="true">·</span> : null}
      {cost ? <span data-testid="session-cost">{cost}</span> : null}
    </span>
  );
}
