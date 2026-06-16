import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConnectionLegend } from './ConnectionLegend';
import type { Registry, Topology } from '../../domain';

const TOPOLOGY: Topology = {
  timestamp: '2026-06-14T00:00:00Z',
  nodes: [
    { id: 'mimir-1', typeId: 'mimir', label: 'mimir', parentId: null, status: 'healthy' },
    { id: 'warden-1', typeId: 'warden', label: 'warden', parentId: null, status: 'healthy' },
    { id: 'warden-2', typeId: 'warden', label: 'warden-b', parentId: null, status: 'healthy' },
  ],
  edges: [],
};

const REGISTRY: Registry = {
  version: 1,
  updatedAt: '2026-06-14T00:00:00Z',
  types: [
    {
      id: 'mimir',
      label: 'Mimir',
      category: 'knowledge',
      rune: '',
      icon: '',
      shape: 'circle',
      color: '#fff',
      size: 1,
      border: 'solid',
      description: '',
      parentTypes: [],
      canContain: [],
      fields: [],
    },
    {
      id: 'warden',
      label: 'Warden',
      category: 'agent',
      rune: '',
      icon: '',
      shape: 'circle',
      color: '#fff',
      size: 1,
      border: 'solid',
      description: '',
      parentTypes: [],
      canContain: [],
      fields: [],
    },
  ],
};

describe('ConnectionLegend', () => {
  it('renders all relationship entries plus fallback node entries', () => {
    render(<ConnectionLegend />);
    expect(screen.getAllByRole('listitem')).toHaveLength(22);
  });

  it('renders each relationship with a data-relation attribute', () => {
    render(<ConnectionLegend />);
    expect(screen.getByTestId('legend-edge-manages')).toHaveAttribute('data-relation', 'manages');
    expect(screen.getByTestId('legend-edge-writes')).toHaveAttribute('data-relation', 'writes');
    expect(screen.getByTestId('legend-edge-reads')).toHaveAttribute('data-relation', 'reads');
    expect(screen.getByTestId('legend-edge-routes_to')).toHaveAttribute(
      'data-relation',
      'routes_to',
    );
    expect(screen.getByTestId('legend-edge-observes')).toHaveAttribute('data-relation', 'observes');
    expect(screen.getByTestId('legend-edge-signals_to')).toHaveAttribute(
      'data-relation',
      'signals_to',
    );
    expect(screen.getByTestId('legend-edge-uses')).toHaveAttribute('data-relation', 'uses');
    expect(screen.getByTestId('legend-edge-exposes')).toHaveAttribute('data-relation', 'exposes');
    expect(screen.getByTestId('legend-edge-member_of')).toHaveAttribute(
      'data-relation',
      'member_of',
    );
    expect(screen.getByTestId('legend-edge-run')).toHaveAttribute('data-relation', 'run');
  });

  it('has accessible region and list labels', () => {
    render(<ConnectionLegend />);
    expect(screen.getByLabelText(/topology legend/i)).toBeInTheDocument();
    expect(screen.getByRole('list', { name: /connection types/i })).toBeInTheDocument();
    expect(screen.getByRole('list', { name: /node types/i })).toBeInTheDocument();
  });

  it('renders semantic relationship label text for each line type', () => {
    render(<ConnectionLegend />);
    expect(screen.getByText('manages')).toBeInTheDocument();
    expect(screen.getByText('writes')).toBeInTheDocument();
    expect(screen.getByText('reads')).toBeInTheDocument();
    expect(screen.getByText('routes')).toBeInTheDocument();
    expect(screen.getByText('observes')).toBeInTheDocument();
    expect(screen.getByText('signals')).toBeInTheDocument();
    expect(screen.getByText('uses')).toBeInTheDocument();
    expect(screen.getByText('exposes')).toBeInTheDocument();
    expect(screen.getByText('membership')).toBeInTheDocument();
    expect(screen.getByText('run flow')).toBeInTheDocument();
  });

  it('renders an SVG swatch for each relationship', () => {
    const { container } = render(<ConnectionLegend />);
    const svgs = container.querySelectorAll('svg.obs-conn-legend__line-svg');
    expect(svgs).toHaveLength(10);
  });

  it('renders SVG markup correctly for animated relationships', () => {
    render(<ConnectionLegend />);
    expect(screen.getByTestId('legend-edge-writes').querySelector('animate')).toBeInTheDocument();
    expect(
      screen.getByTestId('legend-edge-signals_to').querySelector('animate'),
    ).toBeInTheDocument();
  });

  it('renders run flow with circles', () => {
    render(<ConnectionLegend />);
    const runItem = screen.getByTestId('legend-edge-run');
    expect(runItem.querySelector('circle')).toBeInTheDocument();
    expect(runItem.querySelector('g')).toBeInTheDocument();
  });

  it('renders manages with a plain line', () => {
    render(<ConnectionLegend />);
    const managesItem = screen.getByTestId('legend-edge-manages');
    expect(managesItem.querySelector('line')).toBeInTheDocument();
  });

  it('renders node types from topology and registry labels', () => {
    render(<ConnectionLegend topology={TOPOLOGY} registry={REGISTRY} />);

    expect(screen.getByTestId('legend-node-mimir')).toHaveTextContent('Mimir');
    expect(screen.getByTestId('legend-node-warden')).toHaveTextContent('Warden');
    expect(screen.getByTestId('legend-node-warden')).toHaveTextContent('ᚹ');
    expect(screen.getByTestId('legend-node-warden')).toHaveTextContent('2');
    expect(screen.queryByTestId('legend-node-ting')).not.toBeInTheDocument();
  });
});
