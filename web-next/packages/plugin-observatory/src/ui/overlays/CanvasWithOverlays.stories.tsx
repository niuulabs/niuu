import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { EntityDrawer } from './EntityDrawer';
import { EventLog } from './EventLog';
import { ConnectionLegend } from './ConnectionLegend';
import { Minimap } from './Minimap';
import {
  createMockTopologyStream,
  createMockEventStream,
  createMockRegistryRepository,
} from '../../adapters/mock';
import { useTopology } from '../../application/useTopology';
import { useEvents } from '../../application/useEvents';
import { useRegistry } from '../../application/useRegistry';
import type { TopologyNode } from '../../domain';

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function withObservatory() {
  return function ObservatoryWrapper(Story: React.ComponentType) {
    return (
      <QueryClientProvider client={makeClient()}>
        <ServicesProvider
          services={{
            'observatory.topology': createMockTopologyStream(),
            'observatory.events': createMockEventStream(),
            'observatory.registry': createMockRegistryRepository(),
          }}
        >
          <Story />
        </ServicesProvider>
      </QueryClientProvider>
    );
  };
}

/**
 * Canvas-with-overlays harness.
 * Shows all 4 overlay components wired together with live mock data.
 */
function CanvasWithOverlaysDemo() {
  const topology = useTopology();
  const events = useEvents();
  const { data: registry } = useRegistry();
  const [selectedNode, setSelectedNode] = useState<TopologyNode | null>(null);

  return (
    <div
      style={{
        position: 'relative',
        width: '100%',
        height: '100vh',
        background: 'var(--color-bg-primary)',
        overflow: 'hidden',
      }}
    >
      {/* Canvas placeholder — replaced by canvas in NIU-664 */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 'var(--space-4)',
        }}
      >
        <p
          style={{
            color: 'var(--color-text-muted)',
            fontSize: 'var(--text-sm)',
            fontFamily: 'var(--font-mono)',
          }}
        >
          topology canvas — click a node below to open drawer
        </p>

        {/* Clickable node list (interim until canvas is connected) */}
        <ul
          style={{
            listStyle: 'none',
            padding: 0,
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
            gap: 'var(--space-2)',
            maxWidth: 600,
            width: '100%',
          }}
          data-testid="node-list"
        >
          {topology?.nodes.map((node) => (
            <li key={node.id}>
              <button
                onClick={() => setSelectedNode(node)}
                style={{
                  width: '100%',
                  padding: 'var(--space-2)',
                  background: 'var(--color-bg-secondary)',
                  border: '1px solid var(--color-border-subtle)',
                  borderRadius: 'var(--radius-sm)',
                  color: 'var(--color-text-primary)',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 'var(--text-xs)',
                  cursor: 'pointer',
                  textAlign: 'left',
                }}
                data-testid={`node-btn-${node.id}`}
                data-node-type={node.typeId}
              >
                {node.label}
                <br />
                <span style={{ color: 'var(--color-text-muted)' }}>{node.typeId}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      {/* Overlays */}
      <ConnectionLegend />
      <EventLog events={events} />
      <Minimap topology={topology} selectedNodeId={selectedNode?.id} />
      <EntityDrawer
        node={selectedNode}
        topology={topology}
        registry={registry ?? null}
        onClose={() => setSelectedNode(null)}
        onNodeSelect={(n) => setSelectedNode(n)}
      />
    </div>
  );
}

const meta: Meta = {
  title: 'Observatory/CanvasWithOverlays',
  parameters: { layout: 'fullscreen' },
  decorators: [withObservatory()],
};
export default meta;

type Story = StoryObj;

export const Default: Story = {
  render: () => <CanvasWithOverlaysDemo />,
};
