import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Inspector } from './Inspector';
import type { Registry, Topology, TopologyNode } from '../../domain';

function node(id: string, typeId: string, over: Partial<TopologyNode> = {}): TopologyNode {
  return { id, typeId, label: id, parentId: null, status: 'healthy', ...over } as TopologyNode;
}

const registry: Registry = {
  version: 1,
  updatedAt: '2026-08-02T00:00:00Z',
  types: [
    {
      id: 'mimir',
      label: 'Niuu Mímir',
      description: 'The primary shared knowledge base.',
    } as Registry['types'][number],
  ],
};

const mimir = node('mimir-shared', 'mimir', {
  label: 'mímir-shared',
  cluster: 'ymir',
  realm: 'asgard',
  pages: 517,
} as Partial<TopologyNode>);

const topology: Topology = {
  nodes: [
    mimir,
    node('mimir-2', 'mimir', { cluster: 'noatun', realm: 'asgard' }),
    node('b', 'bifrost'),
  ],
  edges: [{ id: 'e1', sourceId: 'mimir-shared', targetId: 'b', kind: 'solid' }],
  timestamp: '2026-08-02T00:00:00Z',
};

describe('Inspector', () => {
  it('prompts for a selection when nothing is selected', () => {
    render(<Inspector node={null} topology={topology} registry={registry} />);

    expect(screen.getByTestId('inspector-empty')).toBeInTheDocument();
  });

  it('heads with the registry kind, the name and the placement', () => {
    render(<Inspector node={mimir} topology={topology} registry={registry} />);

    expect(screen.getByText('Niuu Mímir')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('mímir-shared');
    expect(screen.getByText('ymir · asgard')).toBeInTheDocument();
  });

  it('explains what the entity is from the registry description', () => {
    render(<Inspector node={mimir} topology={topology} registry={registry} />);

    expect(screen.getByText('What it is')).toBeInTheDocument();
    expect(screen.getByText('The primary shared knowledge base.')).toBeInTheDocument();
  });

  it('omits the description block when the registry has none', () => {
    render(<Inspector node={node('b', 'bifrost')} topology={topology} registry={registry} />);

    expect(screen.queryByText('What it is')).not.toBeInTheDocument();
  });

  it('lists only detail rows the adapters actually populated', () => {
    render(<Inspector node={mimir} topology={topology} registry={registry} />);

    // pages was attached; gpu was not, so it gets no row at all.
    expect(screen.getByText('pages')).toBeInTheDocument();
    expect(screen.queryByText('gpu')).not.toBeInTheDocument();
  });

  it('says a node is outside the realms rather than leaving realm blank', () => {
    render(<Inspector node={node('x', 'host')} topology={topology} registry={registry} />);

    expect(screen.getByText('outside the realms')).toBeInTheDocument();
  });

  it('counts the entity among its peers', () => {
    render(<Inspector node={mimir} topology={topology} registry={registry} />);

    expect(screen.getByText('1 of 2')).toBeInTheDocument();
    expect(screen.getByText('The other 1')).toBeInTheDocument();
  });

  it('lists what the entity is connected to', () => {
    render(<Inspector node={mimir} topology={topology} registry={registry} />);

    expect(screen.getByTestId('inspector-connected')).toHaveTextContent('b');
  });

  it('says so when an entity is connected to nothing', () => {
    render(
      <Inspector
        node={node('lonely', 'host')}
        topology={{ ...topology, edges: [] }}
        registry={registry}
      />,
    );

    expect(screen.getByText('Nothing yet.')).toBeInTheDocument();
  });

  it('navigates to a peer when its row is clicked', async () => {
    const onNodeSelect = vi.fn();
    render(
      <Inspector
        node={mimir}
        topology={topology}
        registry={registry}
        onNodeSelect={onNodeSelect}
      />,
    );

    await userEvent.click(screen.getByTestId('insp-peer-mimir-2'));

    expect(onNodeSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'mimir-2' }));
  });

  it('renders the composed footer', () => {
    render(
      <Inspector
        node={mimir}
        topology={topology}
        registry={registry}
        footer={<span>a2a card</span>}
      />,
    );

    expect(screen.getByText('a2a card')).toBeInTheDocument();
  });

  it('falls back to the type id when the registry is absent', () => {
    render(<Inspector node={mimir} topology={topology} registry={null} />);

    // Scoped: the type id also appears in the detail rows and peer kinds.
    expect(screen.getByTestId('inspector-kind')).toHaveTextContent('mimir');
  });
});
