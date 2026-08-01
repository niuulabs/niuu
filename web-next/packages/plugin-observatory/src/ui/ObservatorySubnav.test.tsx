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

  it('renders all 5 filter buttons', () => {
    render(<ObservatorySubnav />);
    expect(screen.getByTestId('filter-all')).toBeInTheDocument();
    expect(screen.getByTestId('filter-agents')).toBeInTheDocument();
    expect(screen.getByTestId('filter-runs')).toBeInTheDocument();
    expect(screen.getByTestId('filter-services')).toBeInTheDocument();
    expect(screen.getByTestId('filter-devices')).toBeInTheDocument();
  });

  it('renders correct total count for "all" filter', () => {
    render(<ObservatorySubnav />);
    // 9 nodes total
    const allBtn = screen.getByTestId('filter-all');
    expect(allBtn).toHaveTextContent('9');
  });

  it('renders agents filter with correct count', () => {
    render(<ObservatorySubnav />);
    // ravn_long only
    const agentsBtn = screen.getByTestId('filter-agents');
    expect(agentsBtn).toHaveTextContent('1');
  });

  it('renders runs filter with correct count', () => {
    render(<ObservatorySubnav />);
    const runsBtn = screen.getByTestId('filter-runs');
    expect(runsBtn).toHaveTextContent('1');
  });

  it('counts only service nodes as services', () => {
    render(<ObservatorySubnav />);
    const servicesBtn = screen.getByTestId('filter-services');
    expect(servicesBtn).toHaveTextContent('1');
  });

  it('renders both realms in the realms section', () => {
    render(<ObservatorySubnav />);
    expect(screen.getByTestId('realm-realm-asgard')).toBeInTheDocument();
    expect(screen.getByTestId('realm-realm-midgard')).toBeInTheDocument();
  });

  it('shows vlan for realms', () => {
    render(<ObservatorySubnav />);
    expect(screen.getByText('vlan 90')).toBeInTheDocument();
  });

  it('renders cluster in clusters section', () => {
    render(<ObservatorySubnav />);
    expect(screen.getByTestId('cluster-cluster-valaskjalf')).toBeInTheDocument();
  });

  it('renders active run in runs section', () => {
    render(<ObservatorySubnav />);
    expect(screen.getByTestId('run-run-1')).toBeInTheDocument();
    expect(screen.getByText('refactor rule engine')).toBeInTheDocument();
  });

  it('calls setFilter when a filter button is clicked', () => {
    render(<ObservatorySubnav />);
    fireEvent.click(screen.getByTestId('filter-agents'));
    expect(mockSetFilter).toHaveBeenCalledWith('agents');
  });

  it('calls setSelected when a realm is clicked', () => {
    render(<ObservatorySubnav />);
    fireEvent.click(screen.getByTestId('realm-realm-asgard'));
    expect(mockSetSelected).toHaveBeenCalledWith('realm-asgard');
  });

  it('calls setSelected when a cluster is clicked', () => {
    render(<ObservatorySubnav />);
    fireEvent.click(screen.getByTestId('cluster-cluster-valaskjalf'));
    expect(mockSetSelected).toHaveBeenCalledWith('cluster-valaskjalf');
  });

  it('renders with no topology (empty state)', () => {
    vi.mocked(useTopology).mockReturnValue(null);
    render(<ObservatorySubnav />);
    // Should still render the subnav without crashing
    expect(screen.getByTestId('observatory-subnav')).toBeInTheDocument();
    // All counts should be 0
    expect(screen.getByTestId('filter-all')).toHaveTextContent('0');
  });

  it('marks active filter with aria-pressed', () => {
    mockUseObservatoryStore.mockReturnValue([
      { selectedId: null, filter: 'agents' },
      { setSelected: mockSetSelected, setFilter: mockSetFilter },
    ]);
    render(<ObservatorySubnav />);
    expect(screen.getByTestId('filter-agents')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('filter-all')).toHaveAttribute('aria-pressed', 'false');
  });

  it('renders selection and run metadata fallbacks', () => {
    vi.mocked(useTopology).mockReturnValue({
      ...MOCK_TOPOLOGY,
      nodes: [
        {
          id: 'realm-no-vlan',
          typeId: 'realm',
          label: 'utgard',
          parentId: null,
          status: 'healthy',
        },
        {
          id: 'cluster-selected',
          typeId: 'cluster',
          label: 'selected cluster',
          parentId: 'realm-no-vlan',
          status: 'healthy',
        },
        {
          id: 'run-forming',
          typeId: 'run',
          label: 'forming fallback',
          parentId: 'cluster-selected',
          status: 'observing',
          state: 'forming',
        },
        {
          id: 'run-unknown',
          typeId: 'run',
          label: 'unknown fallback',
          parentId: 'cluster-selected',
          status: 'observing',
        },
      ],
    });
    mockUseObservatoryStore.mockReturnValue([
      { selectedId: 'realm-no-vlan', filter: 'runs' },
      { setSelected: mockSetSelected, setFilter: mockSetFilter },
    ]);
    const { rerender } = render(<ObservatorySubnav />);

    expect(screen.getByTestId('realm-realm-no-vlan')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByText(/vlan/i)).not.toBeInTheDocument();
    expect(screen.getByText('forming fallback')).toBeInTheDocument();
    expect(screen.getByText('unknown fallback')).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('run-run-forming'));
    expect(mockSetSelected).toHaveBeenCalledWith('run-forming');

    mockUseObservatoryStore.mockReturnValue([
      { selectedId: 'cluster-selected', filter: 'runs' },
      { setSelected: mockSetSelected, setFilter: mockSetFilter },
    ]);
    rerender(<ObservatorySubnav />);
    expect(screen.getByTestId('cluster-cluster-selected')).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('ObservatorySubnav collapsible sections', () => {
  it('renders every rail section as a disclosure', () => {
    render(<ObservatorySubnav />);
    for (const id of ['filter', 'realms', 'clusters']) {
      expect(screen.getByTestId(`subnav-section-${id}`)).toBeInTheDocument();
      expect(screen.getByTestId(`subnav-toggle-${id}`).tagName).toBe('SUMMARY');
    }
  });

  it('opens every section by default', () => {
    render(<ObservatorySubnav />);
    for (const id of ['filter', 'realms', 'clusters']) {
      expect(screen.getByTestId(`subnav-section-${id}`)).toHaveAttribute('open');
    }
  });

  it('collapses a section without disturbing the others', () => {
    render(<ObservatorySubnav />);
    fireEvent.click(screen.getByTestId('subnav-toggle-realms'));

    expect((screen.getByTestId('subnav-section-realms') as HTMLDetailsElement).open).toBe(false);
    expect((screen.getByTestId('subnav-section-filter') as HTMLDetailsElement).open).toBe(true);
    expect((screen.getByTestId('subnav-section-clusters') as HTMLDetailsElement).open).toBe(true);
  });
});
