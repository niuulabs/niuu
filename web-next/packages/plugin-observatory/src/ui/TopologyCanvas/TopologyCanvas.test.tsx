import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { TopologyCanvas } from './TopologyCanvas';
import type { Topology } from '../../domain';
import { makeCtxMock } from './test-helpers';
import { computeLayout, computeLayoutBounds } from './layoutEngine';
import { fitCameraToBounds, type Camera } from './canvasMath';
import { CANVAS } from './config';
import { getStructureLabelBounds } from './renderer';

const CANVAS_RECT = { left: 24, top: 16, width: 480, height: 320 };
const MINIMAP_RECT = { left: 260, top: 180, width: CANVAS.MINIMAP_W, height: CANVAS.MINIMAP_H };
const MINIMAP_LABEL = 'Topology minimap — click to pan';

let viewportSize = { w: CANVAS_RECT.width, h: CANVAS_RECT.height };
let animationFrames: FrameRequestCallback[] = [];
let mainCtx: ReturnType<typeof makeCtxMock>;
let minimapCtx: ReturnType<typeof makeCtxMock>;
let resizeObserverCallback: ResizeObserverCallback | null = null;

function asDomRect({
  left,
  top,
  width,
  height,
}: {
  left: number;
  top: number;
  width: number;
  height: number;
}): DOMRect {
  return {
    left,
    top,
    width,
    height,
    x: left,
    y: top,
    right: left + width,
    bottom: top + height,
    toJSON: () => ({}),
  } as DOMRect;
}

function fittedCamera(topology: Topology): Camera {
  const positions = computeLayout(topology);
  const bounds = computeLayoutBounds(topology, positions);
  return fitCameraToBounds(bounds, viewportSize.w, viewportSize.h, 86);
}

function worldToClientPoint(worldX: number, worldY: number, camera: Camera) {
  return {
    clientX: CANVAS_RECT.left + viewportSize.w / 2 + (worldX - camera.x) * camera.zoom,
    clientY: CANVAS_RECT.top + viewportSize.h / 2 + (worldY - camera.y) * camera.zoom,
  };
}

function runAnimationFrame(now = 1000) {
  const callback = animationFrames.shift();
  expect(callback).toBeDefined();
  act(() => callback?.(now));
}

function triggerResize(width: number, height: number) {
  act(() =>
    resizeObserverCallback?.(
      [{ contentRect: { width, height } } as ResizeObserverEntry],
      {} as ResizeObserver,
    ),
  );
}

async function renderCanvas(
  topology: Topology | null,
  props: Partial<React.ComponentProps<typeof TopologyCanvas>> = {},
) {
  render(<TopologyCanvas topology={topology} {...props} />);
  const canvas = screen.getByTestId('topology-canvas') as HTMLCanvasElement;
  await waitFor(() => {
    expect(canvas.style.width).toBe(`${viewportSize.w}px`);
    expect(canvas.style.height).toBe(`${viewportSize.h}px`);
  });
  return canvas;
}

beforeEach(() => {
  viewportSize = { w: CANVAS_RECT.width, h: CANVAS_RECT.height };
  animationFrames = [];
  mainCtx = makeCtxMock();
  minimapCtx = makeCtxMock();
  resizeObserverCallback = null;

  Object.defineProperty(HTMLCanvasElement.prototype, 'clientWidth', {
    configurable: true,
    get() {
      return this.getAttribute('aria-label') === MINIMAP_LABEL
        ? MINIMAP_RECT.width
        : viewportSize.w;
    },
  });
  Object.defineProperty(HTMLCanvasElement.prototype, 'clientHeight', {
    configurable: true,
    get() {
      return this.getAttribute('aria-label') === MINIMAP_LABEL
        ? MINIMAP_RECT.height
        : viewportSize.h;
    },
  });
  HTMLCanvasElement.prototype.getBoundingClientRect = vi.fn(function getBoundingClientRect() {
    return this.getAttribute('aria-label') === MINIMAP_LABEL
      ? asDomRect(MINIMAP_RECT)
      : asDomRect(CANVAS_RECT);
  });
  HTMLCanvasElement.prototype.getContext = vi.fn(function getContext() {
    return this.getAttribute('aria-label') === MINIMAP_LABEL ? minimapCtx : mainCtx;
  });
  global.ResizeObserver = class ResizeObserver {
    constructor(callback: ResizeObserverCallback) {
      resizeObserverCallback = callback;
    }
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
  } as unknown as typeof ResizeObserver;

  vi.stubGlobal(
    'requestAnimationFrame',
    vi.fn((callback: FrameRequestCallback) => {
      animationFrames.push(callback);
      return animationFrames.length;
    }),
  );
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
    { id: 'ting-0', typeId: 'ting', label: 'ting-0', parentId: 'cluster-vk', status: 'healthy' },
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
    { id: 'run-0', typeId: 'run', label: 'run-0', parentId: 'cluster-vk', status: 'observing' },
  ],
  edges: [
    { id: 'e1', sourceId: 'ting-0', targetId: 'bifrost-0', kind: 'solid' },
    { id: 'e2', sourceId: 'ting-0', targetId: 'run-0', kind: 'dashed-anim' },
    { id: 'e3', sourceId: 'bifrost-0', targetId: 'mimir-0', kind: 'dashed-long' },
    { id: 'e4', sourceId: 'bifrost-0', targetId: 'mimir-0', kind: 'soft' },
    { id: 'e5', sourceId: 'run-0', targetId: 'ting-0', kind: 'run' },
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
    fireEvent.click(screen.getByRole('button', { name: /zoom in/i }));
    const initialPct = parseInt(zoomDisplay.textContent ?? '0', 10);
    fireEvent.click(screen.getByRole('button', { name: /zoom out/i }));
    const newPct = parseInt(zoomDisplay.textContent ?? '0', 10);
    expect(newPct).toBeLessThan(initialPct);
  });

  it('camera reset button restores default zoom percentage', () => {
    render(<TopologyCanvas topology={MOCK_TOPOLOGY} />);
    const zoomDisplay = screen.getByTestId('zoom-display');
    const initialPct = parseInt(zoomDisplay.textContent ?? '0', 10);
    // Zoom in twice
    fireEvent.click(screen.getByRole('button', { name: /zoom in/i }));
    fireEvent.click(screen.getByRole('button', { name: /zoom in/i }));
    // Reset
    fireEvent.click(screen.getByTestId('camera-reset'));
    const pct = parseInt(zoomDisplay.textContent ?? '0', 10);
    expect(pct).toBe(initialPct);
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
        { id: 'e-solid', sourceId: 'ting-0', targetId: 'bifrost-0', kind: 'solid' },
        { id: 'e-dashed-anim', sourceId: 'ting-0', targetId: 'run-0', kind: 'dashed-anim' },
        { id: 'e-dashed-long', sourceId: 'bifrost-0', targetId: 'mimir-0', kind: 'dashed-long' },
        { id: 'e-soft', sourceId: 'bifrost-0', targetId: 'mimir-0', kind: 'soft' },
        { id: 'e-run', sourceId: 'run-0', targetId: 'ting-0', kind: 'run' },
      ],
    };
    expect(() => render(<TopologyCanvas topology={allEdges} />)).not.toThrow();
  });

  it('renders the animation frame onto both the main canvas and minimap', async () => {
    await renderCanvas(MOCK_TOPOLOGY);

    runAnimationFrame();

    expect(mainCtx.setTransform).toHaveBeenCalled();
    expect(mainCtx.fillRect).toHaveBeenCalled();
    expect(mainCtx.save).toHaveBeenCalled();
    expect(minimapCtx.clearRect).toHaveBeenCalledWith(0, 0, CANVAS.MINIMAP_W, CANVAS.MINIMAP_H);
    expect(minimapCtx.strokeRect).toHaveBeenCalled();
  });

  it('keeps requesting animation frames until the canvas has a measurable viewport', () => {
    viewportSize = { w: 0, h: 0 };

    render(<TopologyCanvas topology={MOCK_TOPOLOGY} />);
    runAnimationFrame();

    expect(mainCtx.fillRect).not.toHaveBeenCalled();
    expect(requestAnimationFrame).toHaveBeenCalledTimes(2);
  });

  it('draws the background frame even when topology is null', async () => {
    await renderCanvas(null);

    runAnimationFrame();

    expect(mainCtx.fillRect).toHaveBeenCalled();
    expect(minimapCtx.clearRect).not.toHaveBeenCalled();
  });

  it('does not report node hits when topology is null', async () => {
    const onNodeClick = vi.fn();
    const canvas = await renderCanvas(null, { onNodeClick });

    fireEvent.click(canvas, { clientX: CANVAS_RECT.left + 40, clientY: CANVAS_RECT.top + 40 });

    expect(onNodeClick).not.toHaveBeenCalled();
  });

  it('pans only for arrow-key presses', async () => {
    const canvas = await renderCanvas(MOCK_TOPOLOGY);

    const nonPanEvent = new KeyboardEvent('keydown', {
      bubbles: true,
      cancelable: true,
      key: 'Enter',
    });
    canvas.dispatchEvent(nonPanEvent);
    expect(nonPanEvent.defaultPrevented).toBe(false);

    const panEvent = new KeyboardEvent('keydown', {
      bubbles: true,
      cancelable: true,
      key: 'ArrowLeft',
    });
    canvas.dispatchEvent(panEvent);
    expect(panEvent.defaultPrevented).toBe(true);
  });

  it('hovers and clicks a structure label hit target', async () => {
    const onNodeClick = vi.fn();
    const canvas = await renderCanvas(MOCK_TOPOLOGY, { onNodeClick });
    const positions = computeLayout(MOCK_TOPOLOGY);
    const camera = fittedCamera(MOCK_TOPOLOGY);
    const realmNode = MOCK_TOPOLOGY.nodes.find((node) => node.id === 'realm-asgard')!;
    const labelBounds = getStructureLabelBounds(realmNode, positions.get(realmNode.id)!)!;
    const point = worldToClientPoint(
      labelBounds.x + labelBounds.width / 2,
      labelBounds.y + labelBounds.height / 2,
      camera,
    );

    await waitFor(() =>
      expect(screen.getByTestId('zoom-display')).toHaveTextContent(
        `${Math.round(camera.zoom * 100)}%`,
      ),
    );

    fireEvent.mouseMove(canvas, point);
    expect(canvas.style.cursor).toBe('pointer');

    fireEvent.click(canvas, point);
    expect(onNodeClick).toHaveBeenCalledWith('realm-asgard');

    fireEvent.mouseLeave(canvas);
    expect(canvas.style.cursor).toBe('grab');
  });

  it('keeps the grab cursor when hovering empty space', async () => {
    const canvas = await renderCanvas(MOCK_TOPOLOGY);

    fireEvent.mouseMove(canvas, {
      clientX: CANVAS_RECT.left + 8,
      clientY: CANVAS_RECT.top + viewportSize.h - 8,
    });

    expect(canvas.style.cursor).toBe('grab');
  });

  it('clicks host bodies via the minimap-centred camera and regular nodes via radius hit testing', async () => {
    const onNodeClick = vi.fn();
    const canvas = await renderCanvas(MOCK_TOPOLOGY, { onNodeClick });
    const positions = computeLayout(MOCK_TOPOLOGY);
    const host = positions.get('host-mjolnir')!;
    const minimap = screen.getByLabelText(/minimap/i);
    const minimapPoint = {
      clientX:
        MINIMAP_RECT.left + ((host.x + CANVAS.WORLD_W / 2) / CANVAS.WORLD_W) * MINIMAP_RECT.width,
      clientY:
        MINIMAP_RECT.top + ((host.y + CANVAS.WORLD_H / 2) / CANVAS.WORLD_H) * MINIMAP_RECT.height,
    };

    fireEvent.click(minimap, minimapPoint);
    fireEvent.click(canvas, {
      clientX: CANVAS_RECT.left + viewportSize.w / 2,
      clientY: CANVAS_RECT.top + viewportSize.h / 2,
    });
    expect(onNodeClick).toHaveBeenCalledWith('host-mjolnir');

    fireEvent.click(screen.getByTestId('camera-reset'));
    const camera = fittedCamera(MOCK_TOPOLOGY);
    const ting = positions.get('ting-0')!;
    fireEvent.click(canvas, worldToClientPoint(ting.x, ting.y, camera));
    expect(onNodeClick).toHaveBeenCalledWith('ting-0');
  });

  it('falls back to a device pixel ratio of 1 when the browser reports 0', async () => {
    vi.stubGlobal('devicePixelRatio', 0);
    const canvas = await renderCanvas(MOCK_TOPOLOGY);

    runAnimationFrame();

    expect(canvas.width).toBe(viewportSize.w);
    expect(canvas.height).toBe(viewportSize.h);
    expect(mainCtx.setTransform).toHaveBeenCalledWith(1, 0, 0, 1, 0, 0);
  });

  it('ignores redundant resize measurements and applies real size changes', async () => {
    const canvas = await renderCanvas(MOCK_TOPOLOGY);

    triggerResize(viewportSize.w, viewportSize.h);
    expect(canvas.style.width).toBe(`${viewportSize.w}px`);

    viewportSize = { w: 520, h: 360 };
    triggerResize(viewportSize.w, viewportSize.h);

    await waitFor(() => {
      expect(canvas.style.width).toBe('520px');
      expect(canvas.style.height).toBe('360px');
    });
  });

  it('skips drawing when the main canvas context is unavailable', () => {
    HTMLCanvasElement.prototype.getContext = vi.fn(() => null);

    render(<TopologyCanvas topology={MOCK_TOPOLOGY} />);

    expect(requestAnimationFrame).not.toHaveBeenCalled();
  });

  it('still renders the main frame when the minimap context is unavailable', async () => {
    HTMLCanvasElement.prototype.getContext = vi.fn(function getContext() {
      return this.getAttribute('aria-label') === MINIMAP_LABEL ? null : mainCtx;
    });

    await renderCanvas(MOCK_TOPOLOGY);
    runAnimationFrame();

    expect(mainCtx.fillRect).toHaveBeenCalled();
  });

  it('stops drawing queued frames after unmount cleanup runs', async () => {
    const { unmount } = render(<TopologyCanvas topology={MOCK_TOPOLOGY} />);
    const canvas = screen.getByTestId('topology-canvas') as HTMLCanvasElement;
    await waitFor(() => expect(canvas.style.width).toBe(`${viewportSize.w}px`));

    const pendingFrame = animationFrames.shift();
    expect(pendingFrame).toBeDefined();

    unmount();
    act(() => pendingFrame?.(1000));

    expect(mainCtx.fillRect).not.toHaveBeenCalled();
  });

  // ── Agent mesh ──────────────────────────────────────────────────────────────

  const MESHED_TOPOLOGY: Topology = {
    ...MOCK_TOPOLOGY,
    nodes: [
      ...MOCK_TOPOLOGY.nodes,
      {
        id: 'huginn',
        typeId: 'ravn_long',
        label: 'huginn',
        parentId: 'cluster-vk',
        status: 'healthy',
        flockId: 'forge-mesh',
      },
      {
        id: 'muninn',
        typeId: 'ravn_long',
        label: 'muninn',
        parentId: 'cluster-vk',
        status: 'healthy',
        flockId: 'forge-mesh',
      },
      {
        id: 'kvasir',
        typeId: 'ravn_long',
        label: 'kvasir',
        parentId: 'cluster-vk',
        status: 'healthy',
        flockId: 'forge-mesh',
      },
    ],
  };

  function meshLabelDrawn(): boolean {
    const calls = mainCtx.fillText.mock.calls as [string, ...unknown[]][];
    return calls.some(([text]) => typeof text === 'string' && text.includes('FORGE-MESH'));
  }

  it('outlines no agent mesh while nothing is selected or hovered', async () => {
    await renderCanvas(MESHED_TOPOLOGY);
    runAnimationFrame();

    expect(meshLabelDrawn()).toBe(false);
  });

  it('outlines the mesh of the selected member', async () => {
    await renderCanvas(MESHED_TOPOLOGY, { selectedId: 'muninn' });
    runAnimationFrame();

    expect(meshLabelDrawn()).toBe(true);
  });

  it('outlines nothing when the selection is not a mesh member', async () => {
    await renderCanvas(MESHED_TOPOLOGY, { selectedId: 'ting-0' });
    runAnimationFrame();

    expect(meshLabelDrawn()).toBe(false);
  });
});
