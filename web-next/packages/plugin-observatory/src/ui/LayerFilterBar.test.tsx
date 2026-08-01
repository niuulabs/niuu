import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LayerFilterBar } from './LayerFilterBar';
import { __resetObservatoryStore, getObservatoryStore } from '../application/useObservatoryStore';
import { EDGE_LAYERS } from '../domain';
import type { Topology } from '../domain';

const TOPOLOGY: Topology = {
  timestamp: '2026-08-01T12:00:00Z',
  nodes: [],
  edges: [
    { id: 'a', sourceId: '1', targetId: '2', kind: 'run', relationType: 'member_of' },
    { id: 'b', sourceId: '1', targetId: '3', kind: 'dashed-long', relationType: 'writes' },
    { id: 'c', sourceId: '1', targetId: '4', kind: 'dashed-long', relationType: 'reads' },
    { id: 'd', sourceId: '1', targetId: '5', kind: 'solid', relationType: 'routes_to' },
    { id: 'e', sourceId: '1', targetId: '6', kind: 'soft' },
  ],
};

beforeEach(() => {
  __resetObservatoryStore();
});

describe('LayerFilterBar', () => {
  it('renders a toggle for every layer', () => {
    render(<LayerFilterBar topology={TOPOLOGY} />);
    for (const layer of EDGE_LAYERS) {
      expect(screen.getByTestId(`layer-toggle-${layer}`)).toBeInTheDocument();
    }
  });

  it('counts the edges in each layer from the live topology', () => {
    render(<LayerFilterBar topology={TOPOLOGY} />);
    expect(screen.getByTestId('layer-toggle-memory')).toHaveTextContent('2');
    expect(screen.getByTestId('layer-toggle-mesh')).toHaveTextContent('1');
    // The relation-less edge falls to platform rather than vanishing.
    expect(screen.getByTestId('layer-toggle-platform')).toHaveTextContent('1');
  });

  it('shows a zero for a layer with no edges rather than omitting it', () => {
    render(<LayerFilterBar topology={TOPOLOGY} />);
    expect(screen.getByTestId('layer-toggle-signals')).toHaveTextContent('0');
  });

  it('starts with every layer shown', () => {
    render(<LayerFilterBar topology={TOPOLOGY} />);
    for (const layer of EDGE_LAYERS) {
      expect(screen.getByTestId(`layer-toggle-${layer}`)).toHaveAttribute('aria-pressed', 'true');
    }
  });

  it('toggles a layer off and back on', () => {
    render(<LayerFilterBar topology={TOPOLOGY} />);
    const chip = screen.getByTestId('layer-toggle-memory');

    fireEvent.click(chip);
    expect(chip).toHaveAttribute('aria-pressed', 'false');
    expect(getObservatoryStore().read().hiddenLayers.has('memory')).toBe(true);

    fireEvent.click(chip);
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    expect(getObservatoryStore().read().hiddenLayers.has('memory')).toBe(false);
  });

  it('leaves other layers untouched when one is toggled', () => {
    render(<LayerFilterBar topology={TOPOLOGY} />);
    fireEvent.click(screen.getByTestId('layer-toggle-memory'));
    expect(screen.getByTestId('layer-toggle-mesh')).toHaveAttribute('aria-pressed', 'true');
  });

  it('restores everything with show all', () => {
    render(<LayerFilterBar topology={TOPOLOGY} />);
    fireEvent.click(screen.getByTestId('layer-toggle-memory'));
    fireEvent.click(screen.getByTestId('layer-toggle-mesh'));

    const showAll = screen.getByTestId('layer-toggle-all');
    expect(showAll).not.toBeDisabled();
    fireEvent.click(showAll);

    expect(getObservatoryStore().read().hiddenLayers.size).toBe(0);
    expect(showAll).toBeDisabled();
  });

  it('disables show all while nothing is hidden', () => {
    render(<LayerFilterBar topology={TOPOLOGY} />);
    expect(screen.getByTestId('layer-toggle-all')).toBeDisabled();
  });

  it('renders zero counts when there is no topology yet', () => {
    render(<LayerFilterBar topology={null} />);
    expect(screen.getByTestId('layer-toggle-mesh')).toHaveTextContent('0');
  });
});
