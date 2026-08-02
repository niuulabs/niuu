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

  it('places the entity in one line above its name', () => {
    render(<Inspector node={mimir} topology={topology} registry={registry} />);

    // Kind, engine, cluster, realm — so the reader never has to go down to a
    // table to learn what they are looking at or where it lives.
    expect(screen.getByTestId('inspector-kind')).toHaveTextContent('Niuu Mímir · ymir · asgard');
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('mímir-shared');
  });

  it('never leaves the eyebrow at the kind alone', () => {
    // "Resident" is what the reader already knew from clicking the row. The
    // line has to add placement, or it adds nothing.
    const stray = node('s', 'ravn_long');
    render(<Inspector node={stray} topology={topology} registry={registry} />);

    expect(screen.getByTestId('inspector-kind')).toHaveTextContent('local');
  });

  it('names the engine in the eyebrow when the adapter reported one', () => {
    const resident = node('r', 'ravn_long', { engine: 'hermes', cluster: 'ymir' });
    render(<Inspector node={resident} topology={topology} registry={registry} />);

    expect(screen.getByTestId('inspector-kind')).toHaveTextContent('engine hermes');
  });

  it('leads with what the entity is doing, not with its fields', () => {
    const resident = node('r', 'ravn_long', { activity: 'compacting learnings/' });
    render(<Inspector node={resident} topology={topology} registry={registry} />);

    expect(screen.getByTestId('inspector-activity')).toHaveTextContent('compacting learnings/');
  });

  it('keeps Runtime to how the thing is run', () => {
    const resident = node('r', 'ravn_long', {
      engine: 'hermes',
      deployment: 'DockerContainer',
      pages: 42,
    });
    render(<Inspector node={resident} topology={topology} registry={registry} />);

    expect(screen.getByText('Runtime')).toBeInTheDocument();
    expect(screen.getByText('DockerContainer')).toBeInTheDocument();
    // `pages` is not runtime — it drops to Detail rather than crowding the top.
    expect(screen.getByText('Detail')).toBeInTheDocument();
  });

  it('lists only detail rows the adapters actually populated', () => {
    render(<Inspector node={mimir} topology={topology} registry={registry} />);

    // pages was attached; gpu was not, so it gets no row at all.
    expect(screen.getByText('pages')).toBeInTheDocument();
    expect(screen.queryByText('gpu')).not.toBeInTheDocument();
  });

  it('lists the mesh a resident peers in, not others of its type', () => {
    // Two residents sharing a type share nothing. Two sharing a mesh share
    // their findings, which is the relationship worth a section.
    const a = node('a', 'ravn_long', { flockId: 'ops-mesh' });
    const b = node('b2', 'ravn_long', { flockId: 'ops-mesh' });
    const meshTopology = { ...topology, nodes: [a, b] };

    render(<Inspector node={a} topology={meshTopology} registry={registry} />);

    expect(screen.getByText('Mesh · ops-mesh')).toBeInTheDocument();
    expect(screen.getByTestId('inspector-mesh')).toHaveTextContent('same mesh');
  });

  it('carries counts into the header rather than burying them in a table', () => {
    const resident = node('r', 'ravn_long', { learnedTools: 3, queue: 5, a2aTasks: 0 });
    render(<Inspector node={resident} topology={topology} registry={registry} />);

    expect(screen.getByText('3 learned tools')).toBeInTheDocument();
    expect(screen.getByText('5 queued')).toBeInTheDocument();
    // A zero count is not worth a chip — it says nothing happened.
    expect(screen.queryByText('0 a2a')).not.toBeInTheDocument();
  });

  it('says nothing about a mesh for a node that is in none', () => {
    render(<Inspector node={mimir} topology={topology} registry={registry} />);
    expect(screen.queryByTestId('inspector-mesh')).not.toBeInTheDocument();
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

  it('navigates to a connected node when its row is clicked', async () => {
    const onNodeSelect = vi.fn();
    render(
      <Inspector
        node={mimir}
        topology={topology}
        registry={registry}
        onNodeSelect={onNodeSelect}
      />,
    );

    await userEvent.click(screen.getByTestId('insp-peer-b'));

    expect(onNodeSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'b' }));
  });

  it('offers the resident / card / JSON tabs only when a card slot is composed in', () => {
    const { rerender } = render(<Inspector node={mimir} topology={topology} registry={registry} />);
    expect(screen.queryByTestId('insp-tab-json')).not.toBeInTheDocument();

    rerender(
      <Inspector
        node={mimir}
        topology={topology}
        registry={registry}
        footer={(mode) => <span>card:{mode}</span>}
      />,
    );

    expect(screen.getByTestId('insp-tab-resident')).toHaveAttribute('aria-selected', 'true');
  });

  it('shows the card slot only once a card tab is chosen', async () => {
    render(
      <Inspector
        node={mimir}
        topology={topology}
        registry={registry}
        footer={(mode) => <span>card:{mode}</span>}
      />,
    );
    expect(screen.queryByText(/^card:/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByTestId('insp-tab-json'));

    expect(screen.getByText('card:json')).toBeInTheDocument();
    // The entity body gives way to the card, as the mockup's segmented view does.
    expect(screen.queryByText('Detail')).not.toBeInTheDocument();
  });

  it('falls back to the type id when the registry is absent', () => {
    render(<Inspector node={mimir} topology={topology} registry={null} />);

    // Scoped: the type id also appears in the detail rows and peer kinds.
    expect(screen.getByTestId('inspector-kind')).toHaveTextContent('mimir');
  });
});

describe('Inspector detail rows', () => {
  it('shows a field an adapter attached without the component knowing about it', () => {
    const node = {
      id: 'mimir-ymir',
      typeId: 'mimir',
      label: 'mímir-shared',
      parentId: null,
      status: 'healthy',
      cluster: 'ymir',
      warden: 'mimir-shared-warden-agent 1/1',
      // Nothing in the UI names this key; a new adapter field must surface
      // rather than be silently dropped.
      compaction: 'nightly',
    } as unknown as TopologyNode;

    render(<Inspector node={node} topology={null} registry={null} />);

    expect(screen.getByText('warden')).toBeInTheDocument();
    expect(screen.getByText('mimir-shared-warden-agent 1/1')).toBeInTheDocument();
    expect(screen.getByText('compaction')).toBeInTheDocument();
  });

  it('does not repeat what the head already says', () => {
    const node = {
      id: 'n',
      typeId: 'service',
      label: 'observatory',
      parentId: null,
      status: 'healthy',
      sub: 'ns volundr · 1/1',
    } as unknown as TopologyNode;

    render(<Inspector node={node} topology={null} registry={null} />);
    expect(screen.queryByText('sub')).not.toBeInTheDocument();
  });

  it('renders a list field as a readable line rather than [object Object]', () => {
    const node = {
      id: 'h',
      typeId: 'host',
      label: 'baldr',
      parentId: null,
      status: 'healthy',
      roles: ['control-plane', 'worker'],
    } as unknown as TopologyNode;

    render(<Inspector node={node} topology={null} registry={null} />);
    expect(screen.getByText('control-plane · worker')).toBeInTheDocument();
  });
});
