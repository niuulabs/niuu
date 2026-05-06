import { useTopology } from '../application/useTopology';
import { useEvents } from '../application/useEvents';
import { useRegistry } from '../application/useRegistry';
import { useObservatoryStore } from '../application/useObservatoryStore';
import type { TopologyNode } from '../domain';
import { TopologyCanvas } from './TopologyCanvas';
import { EntityDrawer } from './overlays/EntityDrawer';
import { EventLog } from './overlays/EventLog';
import { Minimap } from './overlays/Minimap';

/**
 * Observatory page — full-viewport topology canvas.
 *
 * Data is sourced from the live topology stream via useTopology().
 * The canvas renders Mímir at (0,0), realms around it, clusters inside
 * realms, hosts on the perimeter, and all 5 connection-line kinds.
 *
 * Selection state is shared via the Observatory store so that the subnav
 * slot can also open entity drawers (e.g. clicking a realm in the subnav).
 */
export function ObservatoryPage() {
  const topology = useTopology();
  const events = useEvents();
  const { data: registry } = useRegistry();
  const [storeState, store] = useObservatoryStore();
  const { selectedId } = storeState;

  const selectedNode: TopologyNode | null =
    selectedId && topology ? (topology.nodes.find((n) => n.id === selectedId) ?? null) : null;

  function handleNodeClick(nodeId: string) {
    store.setSelected(nodeId);
  }

  function handleDrawerClose() {
    store.setSelected(null);
  }

  function handleNodeSelect(node: TopologyNode) {
    store.setSelected(node.id);
  }

  return (
    <div
      data-testid="observatory-page"
      className="niuu-relative niuu-flex niuu-flex-col niuu-h-full niuu-overflow-hidden"
    >
      <TopologyCanvas
        topology={topology}
        onNodeClick={handleNodeClick}
        showMinimap={false}
        className="niuu-flex-1 niuu-min-h-0"
      />

      {/* Accessible hidden node list — keyboard / screen-reader alternative to canvas hit-testing */}
      <ul data-testid="topology-node-list" aria-label="Topology nodes" className="niuu-sr-only">
        {topology?.nodes.map((node) => (
          <li key={node.id}>
            <button
              data-testid={`node-btn-${node.id}`}
              onClick={() => handleNodeClick(node.id)}
              aria-pressed={selectedId === node.id}
            >
              {node.label}
            </button>
          </li>
        ))}
      </ul>

      {/* Canvas overlays — legend is hidden to match web2 canvas view */}
      <Minimap topology={topology} selectedNodeId={selectedNode?.id} />
      <EventLog events={events} />
      <EntityDrawer
        node={selectedNode}
        topology={topology}
        registry={registry ?? null}
        onClose={handleDrawerClose}
        onNodeSelect={handleNodeSelect}
      />
    </div>
  );
}
