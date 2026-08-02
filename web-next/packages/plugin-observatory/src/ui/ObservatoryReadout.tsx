import type { Topology } from '../domain';
import { deriveObservatoryStats } from '../domain/observatoryStats';
import './ObservatoryReadout.css';

interface Props {
  topology: Topology | null;
  /** Messages per minute on the signal feed; null until a rate is known. */
  messageRate?: number | null;
}

/** A count, or a dash when the value was never discovered. */
function Cell({ label, value, tone }: { label: string; value: number | null; tone?: string }) {
  return (
    <div
      className="obs-readout__cell"
      data-testid={`readout-${label.toLowerCase().replace(/[^a-z]+/g, '-')}`}
    >
      <span className="obs-readout__k">{label}</span>
      <span className={`obs-readout__v${tone ? ` obs-readout__v--${tone}` : ''}`}>
        {value === null ? '—' : value.toLocaleString()}
      </span>
    </div>
  );
}

/**
 * Topbar readout — the estate at a glance.
 *
 * Undiscovered counts render as a dash, never zero: "no pod data" and "no
 * pods" are different claims and this row is the first thing trusted.
 */
export function ObservatoryReadout({ topology, messageRate = null }: Props) {
  const stats = deriveObservatoryStats(topology);
  return (
    <div className="obs-readout" data-testid="observatory-readout">
      <Cell label="Realms" value={stats.realms} />
      <Cell label="Clusters" value={stats.clusters} />
      <Cell label="Nodes" value={stats.hosts} />
      <Cell label="Pods" value={stats.pods} />
      <Cell label="Residents" value={stats.residents} tone="spring" />
      <Cell label="Meshes" value={stats.meshes} tone="amber" />
      <Cell label="Mímir" value={stats.mimirs} />
      <Cell label="Msgs / min" value={messageRate} />
    </div>
  );
}
