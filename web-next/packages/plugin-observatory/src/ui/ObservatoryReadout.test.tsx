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

  it('fills the rate in from the topology when the caller passes none', () => {
    // The cell had a prop nobody supplied, so it rendered a dash however busy
    // the estate was.
    const busy = {
      ...topology,
      edges: [
        {
          id: 'e',
          sourceId: 'bifrost',
          targetId: 'model',
          kind: 'solid' as const,
          relationType: 'routes_to' as const,
          ratePerMinute: 4.2,
        },
      ],
    };
    render(<ObservatoryReadout topology={busy} />);

    expect(screen.getByTestId('readout-msgs-min')).toHaveTextContent('4.2');
  });
});
