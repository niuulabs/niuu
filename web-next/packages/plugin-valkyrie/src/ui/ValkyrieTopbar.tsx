import { Activity, Moon } from 'lucide-react';
import { useValkyrieDashboard } from '../application/useValkyrieDashboard';

export function ValkyrieTopbar() {
  const { data } = useValkyrieDashboard();
  const verified = data?.telemetry?.verified;
  const active = verified
    ? (data.telemetry?.runtime.length ?? 0)
    : (data?.valkyries.filter((valkyrie) => valkyrie.status !== 'offline').length ?? 0);
  const dreaming = verified
    ? (data.telemetry?.totals.dreamCyclesStarted ?? 0)
    : (data?.valkyries.filter((valkyrie) => valkyrie.wakefulness === 'dreaming').length ?? 0);
  return (
    <div data-testid="valkyrie-topbar" className="niuu:flex niuu:items-center niuu:gap-2">
      <span className="niuu:inline-flex niuu:items-center niuu:gap-1 niuu:rounded-md niuu:bg-bg-secondary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-muted">
        <Activity size={14} aria-hidden="true" />
        {active}
      </span>
      <span className="niuu:inline-flex niuu:items-center niuu:gap-1 niuu:rounded-md niuu:bg-bg-secondary niuu:px-2 niuu:py-1 niuu:text-xs niuu:text-text-muted">
        <Moon size={14} aria-hidden="true" />
        {dreaming}
      </span>
    </div>
  );
}
