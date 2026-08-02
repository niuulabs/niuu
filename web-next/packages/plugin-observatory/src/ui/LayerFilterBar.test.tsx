import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { LayerFilterBar } from './LayerFilterBar';
import { __resetObservatoryStore, getObservatoryStore } from '../application/useObservatoryStore';
import { EDGE_LAYERS } from '../domain';
import { COMPUTE_CLASSES } from '../domain/computeClass';
import type { Topology } from '../domain';

const TOPOLOGY: Topology = {
  timestamp: '2026-08-01T12:00:00Z',
  nodes: [
    { id: '1', typeId: 'ting', label: 'ting', parentId: null, status: 'healthy', cluster: 'ymir' },
    {
      id: '2',
      typeId: 'ravn_long',
      label: 'kvasir',
      parentId: null,
      status: 'healthy',
      cluster: 'ymir',
    },
    { id: '3', typeId: 'ravn_long', label: 'eldhrímnir', parentId: null, status: 'healthy' },
    {
      id: '4',
      typeId: 'model',
      label: 'claude',
      parentId: null,
      status: 'healthy',
      cluster: 'ymir',
      location: 'external',
    } as Topology['nodes'][number],
  ],
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

  it('restores everything with all', () => {
    render(<LayerFilterBar topology={TOPOLOGY} />);
    fireEvent.click(screen.getByTestId('layer-toggle-memory'));
    fireEvent.click(screen.getByTestId('compute-toggle-outside'));

    const all = screen.getByTestId('filter-all');
    expect(all).not.toBeDisabled();
    fireEvent.click(all);

    expect(getObservatoryStore().read().hiddenLayers.size).toBe(0);
    expect(getObservatoryStore().read().hiddenCompute.size).toBe(0);
    expect(all).toBeDisabled();
  });

  it('disables all while nothing is hidden', () => {
    render(<LayerFilterBar topology={TOPOLOGY} />);
    expect(screen.getByTestId('filter-all')).toBeDisabled();
  });

  it('puts every layer and class down with none', () => {
    render(<LayerFilterBar topology={TOPOLOGY} />);
    fireEvent.click(screen.getByTestId('filter-none'));
    expect(getObservatoryStore().read().hiddenLayers.size).toBe(EDGE_LAYERS.length);
    expect(getObservatoryStore().read().hiddenCompute.size).toBe(COMPUTE_CLASSES.length);
  });

  it('calm keeps the agent story and puts the plumbing down', () => {
    render(<LayerFilterBar topology={TOPOLOGY} />);
    fireEvent.click(screen.getByTestId('filter-calm'));

    const { hiddenLayers, hiddenCompute } = getObservatoryStore().read();
    expect(hiddenLayers.has('platform')).toBe(true);
    expect(hiddenLayers.has('observability')).toBe(true);
    expect(hiddenLayers.has('mesh')).toBe(false);
    expect(hiddenLayers.has('memory')).toBe(false);
    // Calm is about noise, not about hiding hardware.
    expect(hiddenCompute.size).toBe(0);
  });

  it('renders zero counts when there is no topology yet', () => {
    render(<LayerFilterBar topology={null} />);
    expect(screen.getByTestId('layer-toggle-mesh')).toHaveTextContent('0');
  });

  it('counts nodes per compute class', () => {
    render(<LayerFilterBar topology={TOPOLOGY} />);
    // Two live in clusters, the Spark resident does not.
    expect(screen.getByTestId('compute-toggle-k8s')).toHaveTextContent('2');
    expect(screen.getByTestId('compute-toggle-own')).toHaveTextContent('1');
    expect(screen.getByTestId('compute-toggle-outside')).toHaveTextContent('1');
  });

  it('toggles a compute class off and back on', () => {
    render(<LayerFilterBar topology={TOPOLOGY} />);
    const chip = screen.getByTestId('compute-toggle-own');

    fireEvent.click(chip);
    expect(chip).toHaveAttribute('aria-pressed', 'false');
    expect(getObservatoryStore().read().hiddenCompute.has('own')).toBe(true);

    fireEvent.click(chip);
    expect(getObservatoryStore().read().hiddenCompute.has('own')).toBe(false);
  });
});
