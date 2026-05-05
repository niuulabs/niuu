import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ObservatoryTopbar } from './ObservatoryTopbar';

// ── Mock useTopology ──────────────────────────────────────────────────────────

vi.mock('../application/useTopology', () => ({
  useTopology: vi.fn(),
}));

import { useTopology } from '../application/useTopology';
import type { Topology } from '../domain';

const MOCK_TOPOLOGY: Topology = {
  timestamp: '2026-04-19T00:00:00Z',
  edges: [],
  nodes: [
    { id: 'realm-asgard', typeId: 'realm', label: 'asgard', parentId: null, status: 'healthy' },
    { id: 'realm-midgard', typeId: 'realm', label: 'midgard', parentId: null, status: 'healthy' },
    { id: 'ravn-huginn', typeId: 'ravn_long', label: 'huginn', parentId: null, status: 'healthy' },
    { id: 'ravn-muninn', typeId: 'ravn_raid', label: 'muninn', parentId: null, status: 'healthy' },
    { id: 'raid-1', typeId: 'raid', label: 'raid-omega', parentId: null, status: 'observing' },
    { id: 'raid-2', typeId: 'raid', label: 'raid-beta', parentId: null, status: 'observing' },
    { id: 'svc-1', typeId: 'service', label: 'keycloak', parentId: null, status: 'healthy' },
  ],
};

describe('ObservatoryTopbar', () => {
  it('renders the topbar container', () => {
    vi.mocked(useTopology).mockReturnValue(MOCK_TOPOLOGY);
    render(<ObservatoryTopbar />);
    expect(screen.getByTestId('observatory-topbar')).toBeInTheDocument();
  });

  it('renders the realms count label', () => {
    vi.mocked(useTopology).mockReturnValue(MOCK_TOPOLOGY);
    render(<ObservatoryTopbar />);
    expect(screen.getByText('realms')).toBeInTheDocument();
  });

  it('renders the ravens count label', () => {
    vi.mocked(useTopology).mockReturnValue(MOCK_TOPOLOGY);
    render(<ObservatoryTopbar />);
    expect(screen.getByText('ravens')).toBeInTheDocument();
  });

  it('renders the raids count label', () => {
    vi.mocked(useTopology).mockReturnValue(MOCK_TOPOLOGY);
    render(<ObservatoryTopbar />);
    expect(screen.getByText('raids')).toBeInTheDocument();
  });

  it('shows correct realm count (2)', () => {
    vi.mocked(useTopology).mockReturnValue(MOCK_TOPOLOGY);
    render(<ObservatoryTopbar />);
    // 2 realm nodes
    const topbar = screen.getByTestId('observatory-topbar');
    expect(topbar).toHaveTextContent('2');
  });

  it('shows correct raven count (ravn_long + ravn_raid = 2)', () => {
    vi.mocked(useTopology).mockReturnValue(MOCK_TOPOLOGY);
    const { container } = render(<ObservatoryTopbar />);
    // ravens stat value
    const ravensStat = container.querySelector('.obs-topbar__stat--accent');
    expect(ravensStat).not.toBeNull();
    expect(ravensStat).toHaveTextContent('2');
  });

  it('shows correct raid count (2)', () => {
    vi.mocked(useTopology).mockReturnValue(MOCK_TOPOLOGY);
    const { container } = render(<ObservatoryTopbar />);
    const accentStats = container.querySelectorAll('.obs-topbar__stat--accent');
    // Second accent stat is raids
    expect(accentStats[1]).toHaveTextContent('2');
  });

  it('renders zeros when topology is null', () => {
    vi.mocked(useTopology).mockReturnValue(null);
    render(<ObservatoryTopbar />);
    const topbar = screen.getByTestId('observatory-topbar');
    // All three counts should be 0
    const values = topbar.querySelectorAll('strong');
    values.forEach((v) => expect(v.textContent).toBe('0'));
  });

  it('renders zeros when topology has no nodes', () => {
    vi.mocked(useTopology).mockReturnValue({ timestamp: '', nodes: [], edges: [] });
    render(<ObservatoryTopbar />);
    const topbar = screen.getByTestId('observatory-topbar');
    const values = topbar.querySelectorAll('strong');
    values.forEach((v) => expect(v.textContent).toBe('0'));
  });

  it('counts only ravn_long and ravn_raid as ravens', () => {
    const topo: Topology = {
      timestamp: '',
      edges: [],
      nodes: [
        { id: 'r1', typeId: 'ravn_long', label: 'r1', parentId: null, status: 'healthy' },
        { id: 'r2', typeId: 'ravn_raid', label: 'r2', parentId: null, status: 'healthy' },
        { id: 'r3', typeId: 'service', label: 'r3', parentId: null, status: 'healthy' },
      ],
    };
    vi.mocked(useTopology).mockReturnValue(topo);
    const { container } = render(<ObservatoryTopbar />);
    const ravensStat = container.querySelector('.obs-topbar__stat--accent');
    expect(ravensStat).toHaveTextContent('2');
  });
});
