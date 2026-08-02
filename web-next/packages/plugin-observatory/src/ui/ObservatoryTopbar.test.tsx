import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ObservatoryTopbar } from './ObservatoryTopbar';
import { __resetObservatoryStore, getObservatoryStore } from '../application/useObservatoryStore';

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
    { id: 'ravn-muninn', typeId: 'ravn_run', label: 'muninn', parentId: null, status: 'healthy' },
    { id: 'run-1', typeId: 'run', label: 'run-omega', parentId: null, status: 'observing' },
    { id: 'run-2', typeId: 'run', label: 'run-beta', parentId: null, status: 'observing' },
    { id: 'svc-1', typeId: 'service', label: 'keycloak', parentId: null, status: 'healthy' },
  ],
};

beforeEach(() => {
  __resetObservatoryStore();
  vi.mocked(useTopology).mockReturnValue(MOCK_TOPOLOGY);
});

describe('ObservatoryTopbar', () => {
  it('renders the topbar container', () => {
    render(<ObservatoryTopbar />);
    expect(screen.getByTestId('observatory-topbar')).toBeInTheDocument();
  });

  it('carries the readout, so the page never draws a second header', () => {
    render(<ObservatoryTopbar />);
    expect(screen.getByTestId('observatory-readout')).toBeInTheDocument();
    expect(screen.getByTestId('readout-realms')).toHaveTextContent('2');
  });

  it('does not restate a count the readout already gives', () => {
    // The old topbar had its own realms/ravens/runs chips beside the readout's
    // REALMS / RESIDENTS cells — the same estate, counted twice, differently.
    const { container } = render(<ObservatoryTopbar />);
    expect(container.querySelector('.obs-topbar__stat')).toBeNull();
  });

  it('reads a null topology without inventing counts', () => {
    vi.mocked(useTopology).mockReturnValue(null);
    render(<ObservatoryTopbar />);
    expect(screen.getByTestId('readout-realms')).toHaveTextContent('—');
  });

  it('toggles present mode', () => {
    render(<ObservatoryTopbar />);
    const button = screen.getByTestId('present-toggle');
    expect(button).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(button);
    expect(button).toHaveAttribute('aria-pressed', 'true');
    expect(getObservatoryStore().read().presenting).toBe(true);

    fireEvent.click(button);
    expect(getObservatoryStore().read().presenting).toBe(false);
  });
});
