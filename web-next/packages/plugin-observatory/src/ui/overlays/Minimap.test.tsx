import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Minimap } from './Minimap';
import { createMockTopologyStream } from '../../adapters/mock';
import type { Topology, TopologyNode } from '../../domain';

const TOPOLOGY = createMockTopologyStream().getSnapshot()!;

const SINGLE_NODE: Topology = {
  nodes: [{ id: 'n1', typeId: 'realm', label: 'my-realm', parentId: null, status: 'healthy' }],
  edges: [],
  timestamp: '2026-04-19T00:00:00Z',
};

const TWO_NODE_TOPOLOGY: Topology = {
  nodes: [
    { id: 'n1', typeId: 'realm', label: 'realm-a', parentId: null, status: 'healthy' },
    { id: 'n2', typeId: 'cluster', label: 'cluster-a', parentId: 'n1', status: 'degraded' },
  ],
  edges: [{ id: 'e1', sourceId: 'n1', targetId: 'n2', kind: 'solid' }],
  timestamp: '2026-04-19T00:00:00Z',
};

describe('Minimap', () => {
  it('renders empty state when topology is null', () => {
    render(<Minimap topology={null} />);
    expect(screen.getByText('no topology')).toBeInTheDocument();
    expect(screen.getByLabelText(/minimap — no topology/i)).toBeInTheDocument();
  });

  it('renders empty state when topology has no nodes', () => {
    render(<Minimap topology={{ nodes: [], edges: [], timestamp: '' }} />);
    expect(screen.getByText('no topology')).toBeInTheDocument();
  });

  it('renders SVG with correct aria-label when topology is present', () => {
    render(<Minimap topology={SINGLE_NODE} />);
    const svg = screen.getByRole('img');
    expect(svg).toHaveAttribute('aria-label', 'Topology minimap: 1 nodes, 0 edges');
  });

  it('renders node count reflected in aria-label', () => {
    render(<Minimap topology={TOPOLOGY} />);
    const nodeCount = TOPOLOGY.nodes.length;
    const edgeCount = TOPOLOGY.edges.length;
    const svg = screen.getByRole('img');
    expect(svg).toHaveAttribute(
      'aria-label',
      `Topology minimap: ${nodeCount} nodes, ${edgeCount} edges`,
    );
  });

  it('renders circles for each node', () => {
    const { container } = render(<Minimap topology={TWO_NODE_TOPOLOGY} />);
    const circles = container.querySelectorAll('circle[data-node-id]');
    expect(circles).toHaveLength(2);
  });

  it('renders lines for each edge', () => {
    const { container } = render(<Minimap topology={TWO_NODE_TOPOLOGY} />);
    const lines = container.querySelectorAll('line');
    expect(lines).toHaveLength(1);
  });

  it('renders edges that reference missing nodes without crashing', () => {
    const brokenTopology: Topology = {
      nodes: [{ id: 'n1', typeId: 'realm', label: 'r', parentId: null, status: 'healthy' }],
      edges: [{ id: 'e1', sourceId: 'n1', targetId: 'missing', kind: 'solid' }],
      timestamp: '',
    };
    expect(() => render(<Minimap topology={brokenTopology} />)).not.toThrow();
  });

  it('increases circle radius for selected node', () => {
    const { container } = render(<Minimap topology={TWO_NODE_TOPOLOGY} selectedNodeId="n1" />);
    const circles = container.querySelectorAll<SVGCircleElement>('circle[data-node-id]');
    const n1Circle = Array.from(circles).find((c) => c.getAttribute('data-node-id') === 'n1');
    const n2Circle = Array.from(circles).find((c) => c.getAttribute('data-node-id') === 'n2');
    expect(Number(n1Circle?.getAttribute('r'))).toBeGreaterThan(
      Number(n2Circle?.getAttribute('r')),
    );
  });

  it('adds stroke to selected node circle', () => {
    const { container } = render(<Minimap topology={TWO_NODE_TOPOLOGY} selectedNodeId="n1" />);
    const circles = container.querySelectorAll<SVGCircleElement>('circle[data-node-id]');
    const n1Circle = Array.from(circles).find((c) => c.getAttribute('data-node-id') === 'n1');
    const n2Circle = Array.from(circles).find((c) => c.getAttribute('data-node-id') === 'n2');
    expect(n1Circle?.getAttribute('stroke')).toBe('var(--color-text-primary)');
    expect(n2Circle?.getAttribute('stroke')).toBe('none');
  });

  it('renders full seed topology without crashing', () => {
    expect(() => render(<Minimap topology={TOPOLOGY} />)).not.toThrow();
  });

  it('handles all node status colours', () => {
    const statuses = ['healthy', 'degraded', 'failed', 'idle', 'observing', 'unknown'] as const;
    const nodes: TopologyNode[] = statuses.map((s, i) => ({
      id: `n-${i}`,
      typeId: 'service',
      label: s,
      parentId: null,
      status: s,
    }));
    const topo: Topology = { nodes, edges: [], timestamp: '' };
    const { container } = render(<Minimap topology={topo} />);
    const circles = container.querySelectorAll('circle[data-node-id]');
    expect(circles).toHaveLength(statuses.length);
  });

  it('renders single-node topology centred without crashing', () => {
    const { container } = render(<Minimap topology={SINGLE_NODE} />);
    const circles = container.querySelectorAll('circle[data-node-id]');
    expect(circles).toHaveLength(1);
    const cx = Number((circles[0] as SVGCircleElement).getAttribute('cx'));
    const cy = Number((circles[0] as SVGCircleElement).getAttribute('cy'));
    expect(cx).toBe(80); // width / 2 = 160/2
    expect(cy).toBe(60); // height / 2 = 120/2
  });

  it('renders nodes with aria-label equal to node label', () => {
    const { container } = render(<Minimap topology={SINGLE_NODE} />);
    const circle = container.querySelector('circle[data-node-id="n1"]');
    expect(circle).toHaveAttribute('aria-label', 'my-realm');
  });
});
