export interface MiniBarProps {
  value: number;
  label: string;
}

/** Small inline utilization bar with cool/warm/hot color tiers. */
export function MiniBar({ value, label }: MiniBarProps) {
  const color =
    value > 0.85 ? 'niuu-bg-critical' : value > 0.6 ? 'niuu-bg-state-warn' : 'niuu-bg-brand';
  return (
    <div className="niuu-flex niuu-flex-col niuu-gap-0.5 niuu-flex-1">
      <span className="niuu-font-mono niuu-text-[10px] niuu-text-text-faint">{label}</span>
      <div
        className="niuu-h-1 niuu-rounded-full niuu-bg-bg-elevated"
        role="progressbar"
        aria-valuenow={Math.round(value * 100)}
        aria-valuemax={100}
        aria-label={`${label} utilization`}
      >
        <div
          className={`niuu-h-full niuu-rounded-full ${color}`}
          style={{ width: `${(value * 100).toFixed(0)}%` }}
        />
      </div>
    </div>
  );
}
