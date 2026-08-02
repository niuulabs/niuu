import { useMemo } from 'react';
import type { Topology } from '../domain';
import { EDGE_LAYERS, EDGE_LAYER_LABELS, countEdgesByLayer } from '../domain';
import {
  COMPUTE_CLASSES,
  COMPUTE_CLASS_SHORT,
  COMPUTE_CLASS_LABELS,
  countNodesByComputeClass,
} from '../domain/computeClass';
import { CALM_HIDDEN_LAYERS, useObservatoryStore } from '../application/useObservatoryStore';
import './LayerFilterBar.css';

export interface LayerFilterBarProps {
  topology: Topology | null;
}

/**
 * The filter strip — which is also the legend.
 *
 * Two ramps run across this canvas: what a connection is for, and whose
 * silicon a thing runs on. A separate legend panel would have to restate both
 * and then drift from them, so each chip carries its own key — the rule is the
 * stroke that layer draws, the swatch is the hue that compute class uses.
 * Counts come from the live topology, and a class with nothing in it is still
 * listed: an empty count is information too.
 */
export function LayerFilterBar({ topology }: LayerFilterBarProps) {
  const [{ hiddenLayers, hiddenCompute }, store] = useObservatoryStore();

  const layerCounts = useMemo(() => countEdgesByLayer(topology?.edges ?? []), [topology]);
  const computeCounts = useMemo(() => countNodesByComputeClass(topology?.nodes ?? []), [topology]);
  const everythingShown = hiddenLayers.size === 0 && hiddenCompute.size === 0;

  /** Back to the view the Observatory opens in: the agent story, no plumbing. */
  const calm = () => {
    store.setHiddenLayers(new Set(CALM_HIDDEN_LAYERS));
    store.setHiddenCompute(new Set());
  };

  return (
    <div className="obs-layer-filter" role="group" aria-label="Topology filters">
      <span className="obs-layer-filter__legend" aria-hidden="true">
        Layers
      </span>
      <div className="obs-layer-filter__group" role="group" aria-label="Connection layers">
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
              <span className={`obs-layer-filter__rule obs-layer-filter__rule--${layer}`} />
              {EDGE_LAYER_LABELS[layer]}
              <span className="obs-layer-filter__count">{layerCounts[layer]}</span>
            </button>
          );
        })}
      </div>

      <span className="obs-layer-filter__legend" aria-hidden="true">
        Compute
      </span>
      <div className="obs-layer-filter__group" role="group" aria-label="Compute">
        {COMPUTE_CLASSES.map((compute) => {
          const shown = !hiddenCompute.has(compute);
          return (
            <button
              key={compute}
              type="button"
              className="obs-layer-filter__chip"
              data-testid={`compute-toggle-${compute}`}
              aria-pressed={shown}
              title={COMPUTE_CLASS_LABELS[compute]}
              onClick={() => store.toggleCompute(compute)}
            >
              <span className={`obs-layer-filter__swatch obs-layer-filter__swatch--${compute}`} />
              {COMPUTE_CLASS_SHORT[compute]}
              <span className="obs-layer-filter__count">{computeCounts[compute]}</span>
            </button>
          );
        })}
      </div>

      <div className="obs-layer-filter__group obs-layer-filter__group--actions">
        <button
          type="button"
          className="obs-layer-filter__chip obs-layer-filter__chip--action"
          data-testid="filter-calm"
          onClick={calm}
        >
          calm
        </button>
        <button
          type="button"
          className="obs-layer-filter__chip obs-layer-filter__chip--action"
          data-testid="filter-all"
          onClick={() => {
            store.setHiddenLayers(new Set());
            store.setHiddenCompute(new Set());
          }}
          disabled={everythingShown}
        >
          all
        </button>
        <button
          type="button"
          className="obs-layer-filter__chip obs-layer-filter__chip--action"
          data-testid="filter-none"
          onClick={() => {
            store.setHiddenLayers(new Set(EDGE_LAYERS));
            store.setHiddenCompute(new Set(COMPUTE_CLASSES));
          }}
        >
          none
        </button>
      </div>
    </div>
  );
}
