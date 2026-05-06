import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { EntityDrawer } from './EntityDrawer';
import { createMockTopologyStream, createMockRegistryRepository } from '../../adapters/mock';
import type { TopologyNode } from '../../domain';

const TOPOLOGY = createMockTopologyStream().getSnapshot()!;
// Registry loaded synchronously from seed
const REGISTRY = await createMockRegistryRepository().getRegistry();

function Controlled({ node, label }: { node: TopologyNode; label: string }) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<TopologyNode | null>(null);

  return (
    <div style={{ padding: 'var(--space-6)' }}>
      <button onClick={() => setOpen(true)}>{label}</button>
      <EntityDrawer
        node={open ? (selected ?? node) : null}
        topology={TOPOLOGY}
        registry={REGISTRY}
        onClose={() => {
          setOpen(false);
          setSelected(null);
        }}
        onNodeSelect={(n) => setSelected(n)}
      />
    </div>
  );
}

const meta: Meta<typeof EntityDrawer> = {
  title: 'Observatory/Overlays/EntityDrawer',
  component: EntityDrawer,
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<typeof EntityDrawer>;

export const RealmKind: Story = {
  render: () => (
    <Controlled
      node={TOPOLOGY.nodes.find((n) => n.typeId === 'realm')!}
      label="Open Realm drawer"
    />
  ),
};

export const ClusterKind: Story = {
  render: () => (
    <Controlled
      node={TOPOLOGY.nodes.find((n) => n.typeId === 'cluster')!}
      label="Open Cluster drawer"
    />
  ),
};

export const HostKind: Story = {
  render: () => (
    <Controlled node={TOPOLOGY.nodes.find((n) => n.typeId === 'host')!} label="Open Host drawer" />
  ),
};

export const EntityKind: Story = {
  render: () => (
    <Controlled node={TOPOLOGY.nodes.find((n) => n.typeId === 'tyr')!} label="Open Týr drawer" />
  ),
};

export const RaidKind: Story = {
  render: () => (
    <Controlled node={TOPOLOGY.nodes.find((n) => n.typeId === 'raid')!} label="Open Raid drawer" />
  ),
};

export const AlwaysOpen: Story = {
  render: () => (
    <EntityDrawer
      node={TOPOLOGY.nodes.find((n) => n.typeId === 'realm')!}
      topology={TOPOLOGY}
      registry={REGISTRY}
      onClose={() => {}}
    />
  ),
};
