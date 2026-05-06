import { useMemo } from 'react';
import { useTopology } from '../application/useTopology';
import './ObservatoryTopbar.css';

const RAVN_KINDS = new Set(['ravn_long', 'ravn_raid']);

/**
 * ObservatoryTopbar — topbar-right slot for the Observatory plugin.
 *
 * Renders three stat chips: realms count, ravens count (accented), raids count (accented).
 * Matches the web2 prototype `ObservatoryTopbar` component.
 */
export function ObservatoryTopbar() {
  const topology = useTopology();

  const stats = useMemo(() => {
    const nodes = topology?.nodes ?? [];
    return {
      realms: nodes.filter((n) => n.typeId === 'realm').length,
      ravens: nodes.filter((n) => RAVN_KINDS.has(n.typeId)).length,
      raids: nodes.filter((n) => n.typeId === 'raid').length,
    };
  }, [topology]);

  return (
    <div className="obs-topbar" data-testid="observatory-topbar">
      <div className="obs-topbar__stat">
        <span className="obs-topbar__label">realms</span>
        <strong className="obs-topbar__value">{stats.realms}</strong>
      </div>
      <div className="obs-topbar__stat obs-topbar__stat--accent">
        <span className="obs-topbar__label">ravens</span>
        <strong className="obs-topbar__value">{stats.ravens}</strong>
      </div>
      <div className="obs-topbar__stat obs-topbar__stat--accent">
        <span className="obs-topbar__label">raids</span>
        <strong className="obs-topbar__value">{stats.raids}</strong>
      </div>
    </div>
  );
}
