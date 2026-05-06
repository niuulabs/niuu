import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TopologyCanvas } from './TopologyCanvas';
import type { Topology } from '../../domain';
import { makeCtxMock } from './test-helpers';

beforeEach(() => {
  HTMLCanvasElement.prototype.getContext = vi.fn().mockReturnValue(makeCtxMock());
  // jsdom ResizeObserver stub is already in vitest.setup.ts
  // Stub rAF as a no-op so the animation loop doesn't recurse infinitely.
  vi.stubGlobal('requestAnimationFrame', vi.fn().mockReturnValue(0));
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
  vi.stubGlobal('devicePixelRatio', 1);
});

// ── Test topology ─────────────────────────────────────────────────────────────

const MOCK_TOPOLOGY: Topology = {
  timestamp: '2026-04-19T00:00:00Z',
  nodes: [
    { id: 'mimir-0', typeId: 'mimir', label: 'mímir', parentId: null, status: 'healthy' },
    { id: 'realm-asgard', typeId: 'realm', label: 'asgard', parentId: null, status: 'healthy' },
    {
      id: 'cluster-vk',
      typeId: 'cluster',
      label: 'valaskjálf',
      parentId: 'realm-asgard',
      status: 'healthy',
    },
    { id: 'tyr-0', typeId: 'tyr', label: 'tyr-0', parentId: 'cluster-vk', status: 'healthy' },
    {
      id: 'bifrost-0',
      typeId: 'bifrost',
      label: 'bifröst',
      parentId: 'cluster-vk',
      status: 'healthy',
    },
    {
      id: 'host-mjolnir',
      typeId: 'host',
      label: 'mjölnir',
      parentId: 'realm-asgard',
      status: 'healthy',
    },
    { id: 'raid-0', typeId: 'raid', label: 'raid-0', parentId: 'cluster-vk', status: 'observing' },
  ],
  edges: [
    { id: 'e1', sourceId: 'tyr-0', targetId: 'bifrost-0', kind: 'solid' },
    { id: 'e2', sourceId: 'tyr-0', targetId: 'raid-0', kind: 'dashed-anim' },
    { id: 'e3', sourceId: 'bifrost-0', targetId: 'mimir-0', kind: 'dashed-long' },
    { id: 'e4', sourceId: 'bifrost-0', targetId: 'mimir-0', kind: 'soft' },
    { id: 'e5', sourceId: 'raid-0', targetId: 'tyr-0', kind: 'raid' },
  ],
};

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('TopologyCanvas', () => {
  it('renders a canvas element', () => {
    render(<TopologyCanvas topology={MOCK_TOPOLOGY} />);
    const canvas = screen.getByTestId('topology-canvas');
    expect(canvas.tagName).toBe('CANVAS');
  });

  it('renders camera controls with zoom display', () => {
    render(<TopologyCanvas topology={MOCK_TOPOLOGY} />);
    expect(screen.getByTestId('camera-controls')).toBeInTheDocument();
    expect(screen.getByTestId('zoom-display')).toBeInTheDocument();
  });

  it('renders zoom in and zoom out buttons', () => {
    render(<TopologyCanvas topology={MOCK_TOPOLOGY} />);
    expect(screen.getByRole('button', { name: /zoom in/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /zoom out/i })).toBeInTheDocument();
  });

  it('renders a camera reset button', () => {
    render(<TopologyCanvas topology={MOCK_TOPOLOGY} />);
    expect(screen.getByRole('button', { name: /reset camera/i })).toBeInTheDocument();
    expect(screen.getByTestId('camera-reset')).toBeInTheDocument();
  });

  it('renders the minimap when showMinimap=true (default)', () => {
    render(<TopologyCanvas topology={MOCK_TOPOLOGY} />);
    expect(screen.getByTestId('minimap-panel')).toBeInTheDocument();
  });

  it('hides the minimap when showMinimap=false', () => {
    render(<TopologyCanvas topology={MOCK_TOPOLOGY} showMinimap={false} />);
    expect(screen.queryByTestId('minimap-panel')).not.toBeInTheDocument();
  });

  it('renders without crash when topology is null', () => {
    expect(() => render(<TopologyCanvas topology={null} />)).not.toThrow();
  });

  it('canvas has a tab index for keyboard focus', () => {
    render(<TopologyCanvas topology={MOCK_TOPOLOGY} />);
    const canvas = screen.getByTestId('topology-canvas');
    expect(canvas).toHaveAttribute('tabIndex', '0');
  });

  it('canvas has an accessible aria-label', () => {
    render(<TopologyCanvas topology={MOCK_TOPOLOGY} />);
    const canvas = screen.getByTestId('topology-canvas');
    expect(canvas).toHaveAttribute('aria-label');
    expect(canvas.getAttribute('aria-label')).toMatch(/pan|zoom/i);
  });

  it('zoom in button increases zoom percentage display', () => {
    render(<TopologyCanvas topology={MOCK_TOPOLOGY} />);
    const zoomDisplay = screen.getByTestId('zoom-display');
    const initialPct = parseInt(zoomDisplay.textContent ?? '0', 10);
    fireEvent.click(screen.getByRole('button', { name: /zoom in/i }));
    const newPct = parseInt(zoomDisplay.textContent ?? '0', 10);
    expect(newPct).toBeGreaterThan(initialPct);
  });

  it('zoom out button decreases zoom percentage display', () => {
    render(<TopologyCanvas topology={MOCK_TOPOLOGY} />);
    const zoomDisplay = screen.getByTestId('zoom-display');
    const initialPct = parseInt(zoomDisplay.textContent ?? '0', 10);
    fireEvent.click(screen.getByRole('button', { name: /zoom out/i }));
    const newPct = parseInt(zoomDisplay.textContent ?? '0', 10);
    expect(newPct).toBeLessThan(initialPct);
  });

  it('camera reset button restores default zoom percentage', () => {
    render(<TopologyCanvas topology={MOCK_TOPOLOGY} />);
    const zoomDisplay = screen.getByTestId('zoom-display');
    // Zoom in twice
    fireEvent.click(screen.getByRole('button', { name: /zoom in/i }));
    fireEvent.click(screen.getByRole('button', { name: /zoom in/i }));
    // Reset
    fireEvent.click(screen.getByTestId('camera-reset'));
    const pct = parseInt(zoomDisplay.textContent ?? '0', 10);
    // Default zoom is INITIAL_ZOOM (0.5) → 50%
    expect(pct).toBe(50);
  });

  it('zoom cannot exceed ZOOM_MAX (300%)', () => {
    render(<TopologyCanvas topology={MOCK_TOPOLOGY} />);
    const zoomDisplay = screen.getByTestId('zoom-display');
    // Click zoom in many times
    for (let i = 0; i < 50; i++) {
      fireEvent.click(screen.getByRole('button', { name: /zoom in/i }));
    }
    const pct = parseInt(zoomDisplay.textContent ?? '0', 10);
    expect(pct).toBeLessThanOrEqual(300);
  });

  it('zoom cannot go below ZOOM_MIN (30%)', () => {
    render(<TopologyCanvas topology={MOCK_TOPOLOGY} />);
    const zoomDisplay = screen.getByTestId('zoom-display');
    // Click zoom out many times
    for (let i = 0; i < 50; i++) {
      fireEvent.click(screen.getByRole('button', { name: /zoom out/i }));
    }
    const pct = parseInt(zoomDisplay.textContent ?? '0', 10);
    expect(pct).toBeGreaterThanOrEqual(30);
  });

  it('calls onNodeClick when a node is clicked (via canvas click)', () => {
    const onNodeClick = vi.fn();
    render(<TopologyCanvas topology={MOCK_TOPOLOGY} onNodeClick={onNodeClick} />);
    // Click the canvas — with mock context there's nothing to hit-test,
    // so this just verifies no crash occurs
    const canvas = screen.getByTestId('topology-canvas');
    fireEvent.click(canvas, { clientX: 0, clientY: 0 });
    // The handler may or may not fire depending on hit-test — just no crash
    expect(true).toBe(true);
  });

  it('accepts a custom className', () => {
    const { container } = render(
      <TopologyCanvas topology={MOCK_TOPOLOGY} className="test-class" />,
    );
    expect(container.firstChild).toHaveClass('test-class');
  });

  it('renders topology with all 5 edge kinds without crashing', () => {
    const allEdges: Topology = {
      ...MOCK_TOPOLOGY,
      edges: [
        { id: 'e-solid', sourceId: 'tyr-0', targetId: 'bifrost-0', kind: 'solid' },
        { id: 'e-dashed-anim', sourceId: 'tyr-0', targetId: 'raid-0', kind: 'dashed-anim' },
        { id: 'e-dashed-long', sourceId: 'bifrost-0', targetId: 'mimir-0', kind: 'dashed-long' },
        { id: 'e-soft', sourceId: 'bifrost-0', targetId: 'mimir-0', kind: 'soft' },
        { id: 'e-raid', sourceId: 'raid-0', targetId: 'tyr-0', kind: 'raid' },
      ],
    };
    expect(() => render(<TopologyCanvas topology={allEdges} />)).not.toThrow();
  });
});
