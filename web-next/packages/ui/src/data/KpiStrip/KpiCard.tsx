import { type ReactNode } from 'react';
import { cn } from '../../utils/cn';
import './KpiStrip.css';

export type KpiDeltaTrend = 'up' | 'down' | 'neutral';

export interface KpiCardProps {
  label: string;
  value: ReactNode;
  delta?: string;
  deltaTrend?: KpiDeltaTrend;
  deltaLabel?: string;
  sparkline?: ReactNode;
  className?: string;
}

export function KpiCard({
  label,
  value,
  delta,
  deltaTrend = 'neutral',
  deltaLabel,
  sparkline,
  className,
}: KpiCardProps) {
  return (
    <div className={cn('niuu-kpi-card', className)}>
      <div className="niuu-kpi-card__header">
        <span className="niuu-kpi-card__label">{label}</span>
      </div>
      <div className="niuu-kpi-card__value">{value}</div>
      {delta !== undefined && (
        <div
          className={cn('niuu-kpi-card__delta', `niuu-kpi-card__delta--${deltaTrend}`)}
          title={deltaLabel}
        >
          <span className="niuu-kpi-card__delta-arrow" aria-hidden="true">
            {deltaTrend === 'up' ? '▲' : deltaTrend === 'down' ? '▼' : '—'}
          </span>
          <span>{delta}</span>
          {deltaLabel && <span className="niuu-kpi-card__delta-label">{deltaLabel}</span>}
        </div>
      )}
      {sparkline && <div className="niuu-kpi-card__sparkline">{sparkline}</div>}
    </div>
  );
}
