import { useMemo } from 'react';
import type { Topology } from '../domain';
import { EDGE_LAYERS, EDGE_LAYER_LABELS, countEdgesByLayer } from '../domain';
import { useObservatoryStore } from '../application/useObservatoryStore';
import './LayerFilterBar.css';

export interface LayerFilterBarProps {
  topology: Topology | null;
}

/**
 * Toggles for the connection layers.
 *
 * The topology carries far more relationships than can be read at once, so the
 * operator needs to be able to put some down. Counts come from the live
 * topology rather than being hard-coded, and a layer with no edges is still
 * listed — its absence is information too.
 */
export function LayerFilterBar({ topology }: LayerFilterBarProps) {
  const [{ hiddenLayers }, store] = useObservatoryStore();

  const counts = useMemo(() => countEdgesByLayer(topology?.edges ?? []), [topology]);
  const allShown = hiddenLayers.size === 0;

  return (
    <div className="obs-layer-filter" role="group" aria-label="Connection layers">
      {EDGE_LAYERS.map((layer) => {
        const shown = !hiddenLayers.has(layer);
        return (
          <button
            key={layer}
            type="button"
            className="obs-layer-filter__chip"
            data-testid={`layer-toggle-${layer}`}
            aria-pressed={shown}
            onClick={() => store.toggleLayer(layer)}
          >
            <span className={`obs-layer-filter__swatch obs-layer-filter__swatch--${layer}`} />
            {EDGE_LAYER_LABELS[layer]}
            <span className="obs-layer-filter__count">{counts[layer]}</span>
          </button>
        );
      })}

      <button
        type="button"
        className="obs-layer-filter__chip obs-layer-filter__chip--action"
        data-testid="layer-toggle-all"
        onClick={() => store.setHiddenLayers(new Set())}
        disabled={allShown}
      >
        show all
      </button>
    </div>
  );
}
