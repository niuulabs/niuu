import { Suspense, lazy } from 'react';
import { useTopology } from '../application/useTopology';
import { useEvents } from '../application/useEvents';
import { useRegistry } from '../application/useRegistry';
import { useObservatoryStore } from '../application/useObservatoryStore';
import type { TopologyNode } from '../domain';
import { TopologyCanvas } from './TopologyCanvas';

import { LayerFilterBar } from './LayerFilterBar';
import { SignalTicker } from './SignalTicker';
import { Inspector } from './overlays/Inspector';
import { AgentCardPanel } from './overlays/AgentCardPanel';
import { humanizeObservatoryText } from './displayLabels';
import './ObservatoryShell.css';

/**
 * The 3D stage, fetched only when someone asks for it.
 *
 * It brings a whole rendering library with it — around a fifth of the app's
 * download, which every operator would otherwise pay for on first load whether
 * or not they ever leave the plan. Split out, the cost lands on the click that
 * asks for it.
 */
const TopologyScene3D = lazy(() =>
  import('./TopologyScene3D').then((module) => ({ default: module.TopologyScene3D })),
);

/**
 * Observatory page.
 *
 * Laid out after `docs/mockups/observatory/index.html`: layer filters across
 * the top, then stage / inspector with the signal ticker under the stage.
 *
 * The mockup's header and left rail are not built here. The shell already
 * gives every plugin a topbar and a rail, and drawing a second set produced
 * exactly what you would expect — the plugin's name twice, its realm count
 * twice, and two Topology labels. The header's Observatory-specific half (the
 * readout, the present toggle) goes in the shell's `topbarRight` slot; the
 * rail goes in `subnav`.
 */
export function ObservatoryPage() {
  const topology = useTopology();
  const events = useEvents();
  const { data: registry } = useRegistry();
  const [storeState, store] = useObservatoryStore();
  const { selectedId, focusId, hiddenLayers, hiddenCompute, presenting, view, motion } = storeState;

  const selectedNode: TopologyNode | null =
    selectedId && topology ? (topology.nodes.find((n) => n.id === selectedId) ?? null) : null;

  function handleNodeClick(nodeId: string | null) {
    // null is a click on empty canvas — the operator is done looking at this.
    store.setSelected(nodeId);
  }

  /**
   * Pick a node up, or put it down if it was already held.
   *
   * Used by the controls that report `aria-pressed`, where pressing again has
   * to release. It is also the only way to stop a mesh pulsing without picking
   * something else instead.
   */
  function toggleSelected(nodeId: string) {
    store.setSelected(nodeId === selectedId ? null : nodeId);
  }

  function handleNodeSelect(node: TopologyNode) {
    toggleSelected(node.id);
  }

  return (
    <div
      data-testid="observatory-page"
      className={`obs-shell${presenting ? ' obs-shell--present' : ''}`}
      data-presenting={presenting}
    >
      <div className="obs-shell__filt">
        <LayerFilterBar topology={topology} />
      </div>

      {/*
        Overlays are absolutely positioned, so they anchor to the stage rather
        than the page — otherwise they cover the filter bar and swallow clicks.
      */}
      <main className="obs-shell__stage">
        {/*
          One estate, one layout, one palette — the stage is a choice of
          viewpoint, not of subject. Both renderers take the same props and
          report selection the same way, so the inspector and the rail neither
          know nor care which one is mounted.
        */}
        {view === '3d' ? (
          <Suspense
            fallback={
              <div className="obs-shell__loading" data-testid="scene3d-loading" role="status">
                Opening the 3D view…
              </div>
            }
          >
            <TopologyScene3D
              topology={topology}
              registry={registry ?? null}
              onNodeClick={handleNodeClick}
              selectedId={selectedId}
              focusId={focusId}
              hiddenLayers={hiddenLayers}
              hiddenCompute={hiddenCompute}
              paused={!motion}
              className="niuu:absolute niuu:inset-0"
            />
          </Suspense>
        ) : (
          <TopologyCanvas
            topology={topology}
            registry={registry ?? null}
            onNodeClick={handleNodeClick}
            selectedId={selectedId}
            focusId={focusId}
            hiddenLayers={hiddenLayers}
            hiddenCompute={hiddenCompute}
            paused={!motion}
            className="niuu:absolute niuu:inset-0"
          />
        )}
      </main>

      <SignalTicker events={events} />

      <aside className="obs-shell__insp" aria-label="Inspector">
        <Inspector
          node={selectedNode}
          topology={topology}
          registry={registry ?? null}
          onNodeSelect={handleNodeSelect}
          footer={(mode) => <AgentCardPanel node={selectedNode} mode={mode} />}
        />
      </aside>

      {/* Accessible hidden node list — keyboard / screen-reader alternative to canvas hit-testing */}
      <ul data-testid="topology-node-list" aria-label="Topology nodes" className="niuu:sr-only">
        {topology?.nodes.map((node) => (
          <li key={node.id}>
            <button
              data-testid={`node-btn-${node.id}`}
              onClick={() => toggleSelected(node.id)}
              aria-pressed={selectedId === node.id}
            >
              {humanizeObservatoryText(node.label)}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
