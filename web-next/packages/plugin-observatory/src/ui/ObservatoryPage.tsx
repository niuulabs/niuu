import { useTopology } from '../application/useTopology';
import { useEvents } from '../application/useEvents';
import { useRegistry } from '../application/useRegistry';
import { useObservatoryStore } from '../application/useObservatoryStore';
import type { TopologyNode } from '../domain';
import { TopologyCanvas } from './TopologyCanvas';
import { LayerFilterBar } from './LayerFilterBar';
import { ObservatoryReadout } from './ObservatoryReadout';
import { SignalTicker } from './SignalTicker';
import { Inspector } from './overlays/Inspector';
import { AgentCardPanel } from './overlays/AgentCardPanel';
import { ConnectionLegend } from './overlays/ConnectionLegend';
import { humanizeObservatoryText } from './displayLabels';
import './ObservatoryShell.css';

/**
 * Observatory page.
 *
 * Laid out after `docs/mockups/observatory/index.html`: a readout across the
 * top, layer filters beneath it, then stage / inspector with the signal ticker
 * under the stage alone.
 *
 * The mockup's left rail lives in the shell's subnav slot rather than here —
 * the platform already gives every plugin one rail, and a second column would
 * just repeat it.
 */
export function ObservatoryPage() {
  const topology = useTopology();
  const events = useEvents();
  const { data: registry } = useRegistry();
  const [storeState, store] = useObservatoryStore();
  const { selectedId, hiddenLayers } = storeState;

  const selectedNode: TopologyNode | null =
    selectedId && topology ? (topology.nodes.find((n) => n.id === selectedId) ?? null) : null;

  function handleNodeClick(nodeId: string) {
    store.setSelected(nodeId);
  }

  function handleNodeSelect(node: TopologyNode) {
    store.setSelected(node.id);
  }

  return (
    <div data-testid="observatory-page" className="obs-shell">
      <header className="obs-shell__top">
        <div className="obs-shell__brand">
          <b>Observatory</b>
          <span>live topology · niuu.world</span>
        </div>
        <ObservatoryReadout topology={topology} />
      </header>

      <div className="obs-shell__filt">
        <LayerFilterBar topology={topology} />
      </div>

      {/*
        Overlays are absolutely positioned, so they anchor to the stage rather
        than the page — otherwise they cover the filter bar and swallow clicks.
      */}
      <main className="obs-shell__stage">
        <TopologyCanvas
          topology={topology}
          onNodeClick={handleNodeClick}
          selectedId={selectedId}
          hiddenLayers={hiddenLayers}
          className="niuu:absolute niuu:inset-0"
        />
        <ConnectionLegend topology={topology} registry={registry ?? null} />
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
              onClick={() => handleNodeClick(node.id)}
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
