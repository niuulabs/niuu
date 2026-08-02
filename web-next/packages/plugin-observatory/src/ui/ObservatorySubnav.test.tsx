import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ObservatorySubnav } from './ObservatorySubnav';

// ── Mock useTopology ──────────────────────────────────────────────────────────

vi.mock('../application/useTopology', () => ({
  useTopology: vi.fn(),
}));

// ── Mock useObservatoryStore ──────────────────────────────────────────────────
// We use a fresh store factory per test to avoid cross-test state pollution.

const { mockSetFilter, mockSetSelected, mockUseObservatoryStore } = vi.hoisted(() => {
  const mockSetFilter = vi.fn();
  const mockSetSelected = vi.fn();
  const mockUseObservatoryStore = vi.fn(() => [
    { selectedId: null, filter: 'all' },
    { setSelected: mockSetSelected, setFilter: mockSetFilter },
  ]);
  return { mockSetFilter, mockSetSelected, mockUseObservatoryStore };
});

vi.mock('../application/useObservatoryStore', () => ({
  useObservatoryStore: mockUseObservatoryStore,
}));

import { useTopology } from '../application/useTopology';
import type { Topology } from '../domain';

const MOCK_TOPOLOGY: Topology = {
  timestamp: '2026-04-19T00:00:00Z',
  edges: [],
  nodes: [
    {
      id: 'realm-asgard',
      typeId: 'realm',
      label: 'asgard',
      parentId: null,
      status: 'healthy',
      vlan: 90,
    },
    {
      id: 'realm-midgard',
      typeId: 'realm',
      label: 'midgard',
      parentId: null,
      status: 'healthy',
      vlan: 60,
    },
    {
      id: 'cluster-valaskjalf',
      typeId: 'cluster',
      label: 'valaskjálf',
      parentId: 'realm-asgard',
      status: 'healthy',
    },
    {
      id: 'ting-0',
      typeId: 'ting',
      label: 'ting-0',
      parentId: 'cluster-valaskjalf',
      status: 'healthy',
    },
    {
      id: 'mimir-1',
      typeId: 'mimir',
      label: 'mimir',
      parentId: 'cluster-valaskjalf',
      status: 'healthy',
    },
    { id: 'ravn-huginn', typeId: 'ravn_long', label: 'huginn', parentId: null, status: 'healthy' },
    {
      id: 'run-1',
      typeId: 'run',
      label: 'run-omega',
      parentId: 'cluster-valaskjalf',
      status: 'observing',
      state: 'working',
      purpose: 'refactor rule engine',
    },
    {
      id: 'svc-1',
      typeId: 'service',
      label: 'keycloak',
      parentId: 'cluster-valaskjalf',
      status: 'healthy',
    },
    { id: 'printer-1', typeId: 'printer', label: 'Mjölnir', parentId: null, status: 'healthy' },
  ],
};

describe('ObservatorySubnav', () => {
  beforeEach(() => {
    vi.mocked(useTopology).mockReturnValue(MOCK_TOPOLOGY);
    mockUseObservatoryStore.mockReturnValue([
      { selectedId: null, filter: 'all' },
      { setSelected: mockSetSelected, setFilter: mockSetFilter },
    ]);
    mockSetFilter.mockClear();
    mockSetSelected.mockClear();
  });

  it('renders the subnav container', () => {
    render(<ObservatorySubnav />);
    expect(screen.getByTestId('observatory-subnav')).toBeInTheDocument();
  });

  it('renders both realms in the realms section', () => {
    render(<ObservatorySubnav />);
    // Realms are named on their clusters' rows now, not listed on their own.
    expect(screen.getByTestId('rail-row-cluster-valaskjalf')).toHaveTextContent('asgard');
  });

  it('names the realm on the cluster row rather than in a section of its own', () => {
    render(<ObservatorySubnav />);
    expect(screen.getByTestId('rail-row-cluster-valaskjalf')).toHaveTextContent('asgard');
  });

  it('carries no node badge for a cluster with no hosts discovered', () => {
    // Zero would read as "this cluster has no machines". Nothing reported any.
    render(<ObservatorySubnav />);
    const row = screen.getByTestId('rail-row-cluster-valaskjalf');
    expect(row.querySelector('.obs-rail__badge')).toBeNull();
  });

  it('renders cluster in clusters section', () => {
    render(<ObservatorySubnav />);
    expect(screen.getByTestId('rail-row-cluster-valaskjalf')).toBeInTheDocument();
  });

  it('calls setSelected when a realm is clicked', () => {
    render(<ObservatorySubnav />);
    fireEvent.click(screen.getByTestId('rail-row-cluster-valaskjalf'));
    expect(mockSetSelected).toHaveBeenCalledWith('cluster-valaskjalf');
  });

  it('calls setSelected when a cluster is clicked', () => {
    render(<ObservatorySubnav />);
    fireEvent.click(screen.getByTestId('rail-row-cluster-valaskjalf'));
    expect(mockSetSelected).toHaveBeenCalledWith('cluster-valaskjalf');
  });
});

describe('ObservatorySubnav collapsible sections', () => {
  it('renders every rail section as a disclosure', () => {
    render(<ObservatorySubnav />);
    for (const id of ['rail-meshes', 'rail-residents', 'rail-mimir', 'rail-clusters']) {
      expect(screen.getByTestId(`subnav-section-${id}`)).toBeInTheDocument();
      expect(screen.getByTestId(`subnav-toggle-${id}`).tagName).toBe('SUMMARY');
    }
  });

  it('opens every section by default', () => {
    render(<ObservatorySubnav />);
    for (const id of ['rail-meshes', 'rail-residents', 'rail-mimir', 'rail-clusters']) {
      expect(screen.getByTestId(`subnav-section-${id}`)).toHaveAttribute('open');
    }
  });

  it('collapses a section without disturbing the others', () => {
    render(<ObservatorySubnav />);
    fireEvent.click(screen.getByTestId('subnav-toggle-rail-clusters'));

    expect((screen.getByTestId('subnav-section-rail-clusters') as HTMLDetailsElement).open).toBe(
      false,
    );
    expect((screen.getByTestId('subnav-section-rail-residents') as HTMLDetailsElement).open).toBe(
      true,
    );
  });
});
