import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { Minimap } from './Minimap';
import { createMockTopologyStream } from '../../adapters/mock';
import type { TopologyNode } from '../../domain';

const TOPOLOGY = createMockTopologyStream().getSnapshot()!;

const meta: Meta<typeof Minimap> = {
  title: 'Observatory/Overlays/Minimap',
  component: Minimap,
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<typeof Minimap>;

export const NoTopology: Story = {
  render: () => (
    <div
      style={{
        width: '100vw',
        height: '100vh',
        background: 'var(--color-bg-primary)',
        position: 'relative',
      }}
    >
      <Minimap topology={null} />
    </div>
  ),
};

export const WithTopology: Story = {
  render: () => (
    <div
      style={{
        width: '100vw',
        height: '100vh',
        background: 'var(--color-bg-primary)',
        position: 'relative',
      }}
    >
      <Minimap topology={TOPOLOGY} />
    </div>
  ),
};

function WithSelectedNodeDemo() {
  const [selected, setSelected] = useState<string | null>('realm-asgard');
  return (
    <div
      style={{
        width: '100vw',
        height: '100vh',
        background: 'var(--color-bg-primary)',
        position: 'relative',
      }}
    >
      <div style={{ padding: 'var(--space-4)' }}>
        {TOPOLOGY.nodes.map((n: TopologyNode) => (
          <button
            key={n.id}
            style={{
              display: 'block',
              marginBottom: 4,
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
            }}
            onClick={() => setSelected(n.id)}
          >
            {n.label} ({n.typeId})
          </button>
        ))}
      </div>
      <Minimap topology={TOPOLOGY} selectedNodeId={selected} />
    </div>
  );
}

export const WithSelectedNode: Story = {
  render: () => <WithSelectedNodeDemo />,
};
