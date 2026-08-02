import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ObservatoryReadout } from './ObservatoryReadout';
import type { Topology, TopologyNode } from '../domain';

function node(id: string, typeId: string): TopologyNode {
  return { id, typeId, label: id, parentId: null, status: 'healthy' } as TopologyNode;
}

const topology: Topology = {
  nodes: [node('r', 'realm'), node('c', 'cluster'), node('h', 'host'), node('m', 'mimir')],
  edges: [],
  timestamp: '2026-08-02T00:00:00Z',
};

describe('ObservatoryReadout', () => {
  it('shows a count for each discovered kind', () => {
    render(<ObservatoryReadout topology={topology} />);

    expect(screen.getByTestId('readout-realms')).toHaveTextContent('1');
    expect(screen.getByTestId('readout-clusters')).toHaveTextContent('1');
    expect(screen.getByTestId('readout-nodes')).toHaveTextContent('1');
  });

  it('renders a dash, not a zero, for values never discovered', () => {
    render(<ObservatoryReadout topology={topology} />);

    expect(screen.getByTestId('readout-pods')).toHaveTextContent('—');
    expect(screen.getByTestId('readout-msgs-min')).toHaveTextContent('—');
  });

  it('shows a message rate once one is known', () => {
    render(<ObservatoryReadout topology={topology} messageRate={138} />);

    expect(screen.getByTestId('readout-msgs-min')).toHaveTextContent('138');
  });

  it('dashes every cell while it is still waiting for a snapshot', () => {
    render(<ObservatoryReadout topology={null} />);

    expect(screen.getByTestId('readout-realms')).toHaveTextContent('—');
    expect(screen.getByTestId('readout-residents')).toHaveTextContent('—');
  });
});
