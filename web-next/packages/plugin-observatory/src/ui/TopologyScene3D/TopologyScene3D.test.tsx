import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { Camera, Scene } from 'three';
import { TopologyScene3D } from './TopologyScene3D';
import { supportsWebGL, type Scene3DRenderer } from './webglRenderer';
import type { Topology, TopologyEdge, TopologyNode } from '../../domain';
import { installCanvas2DMock } from './test-helpers';
import { CAMERA3D } from './scene3dConfig';

// ── Harness ───────────────────────────────────────────────────────────────────

const HOST_RECT = { left: 20, top: 10, width: 800, height: 500 };

let frames: FrameRequestCallback[] = [];
let resizeCallback: ResizeObserverCallback | null = null;
let renders: Array<{ scene: Scene; camera: Camera }> = [];
let disposals = 0;

/**
 * A renderer that records instead of drawing.
 *
 * Everything this component owns — picking, orbiting, framing, the travel to a
 * selection — is geometry and event handling that a GPU contributes nothing to.
 * Injecting the surface is what lets all of it be tested at all.
 */
function fakeRendererFactory(): Scene3DRenderer {
  const domElement = document.createElement('canvas');
  return {
    domElement,
    setSize: vi.fn(),
    setPixelRatio: vi.fn(),
    render: (scene: Scene, camera: Camera) => renders.push({ scene, camera }),
    dispose: () => {
      disposals += 1;
    },
  };
}

const originalGetContext = HTMLCanvasElement.prototype.getContext;
const originalGetBoundingClientRect = Element.prototype.getBoundingClientRect;

function node(
  id: string,
  typeId: string,
  parentId: string | null = null,
  extra: Partial<TopologyNode> = {},
): TopologyNode {
  return { id, typeId, label: id, parentId, status: 'healthy', ...extra };
}

function edge(id: string, sourceId: string, targetId: string, extra: Partial<TopologyEdge> = {}) {
  return { id, sourceId, targetId, kind: 'solid' as const, ...extra };
}

const TOPOLOGY: Topology = {
  timestamp: '2026-08-08T00:00:00Z',
  nodes: [
    node('realm-a', 'realm'),
    node('cluster-a', 'cluster', 'realm-a'),
    node('host-a', 'host', 'cluster-a'),
    node('ravn-a', 'ravn_long', 'host-a', { flockId: 'forge' }),
    node('ravn-b', 'ravn_long', 'host-a', { flockId: 'forge' }),
  ],
  edges: [edge('e1', 'ravn-a', 'ravn-b', { relationType: 'signals_to', ratePerMinute: 8 })],
};

beforeEach(() => {
  frames = [];
  renders = [];
  disposals = 0;
  resizeCallback = null;
  installCanvas2DMock();

  vi.stubGlobal(
    'requestAnimationFrame',
    vi.fn((callback: FrameRequestCallback) => {
      frames.push(callback);
      return frames.length;
    }),
  );
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
  vi.stubGlobal('devicePixelRatio', 2);
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(callback: ResizeObserverCallback) {
        resizeCallback = callback;
      }
      observe = vi.fn();
      unobserve = vi.fn();
      disconnect = vi.fn();
    },
  );

  Element.prototype.getBoundingClientRect = function getRect(this: Element) {
    if ((this as HTMLElement).dataset?.testid === 'topology-scene3d-host') {
      return { ...HOST_RECT, right: 820, bottom: 510, x: 20, y: 10, toJSON: () => ({}) } as DOMRect;
    }
    return originalGetBoundingClientRect.call(this);
  };

  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    get: () => HOST_RECT.width,
  });
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get: () => HOST_RECT.height,
  });
});

afterEach(() => {
  cleanup();
  HTMLCanvasElement.prototype.getContext = originalGetContext;
  Element.prototype.getBoundingClientRect = originalGetBoundingClientRect;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Run `count` animation frames, each one scheduling the next. */
function runFrames(count = 1, start = 0, step = 16) {
  for (let i = 0; i < count; i += 1) {
    const callback = frames.shift();
    if (!callback) return;
    act(() => callback(start + i * step));
  }
}

function renderScene(props: Partial<React.ComponentProps<typeof TopologyScene3D>> = {}) {
  const result = render(
    <TopologyScene3D topology={TOPOLOGY} createRenderer={fakeRendererFactory} {...props} />,
  );
  runFrames(2);
  return result;
}

function host() {
  return screen.getByTestId('topology-scene3d-host');
}

/** Where the camera stood the last time a frame was drawn. */
function eye(): [number, number, number] {
  const last = renders[renders.length - 1];
  if (!last) throw new Error('nothing has been drawn yet');
  return [last.camera.position.x, last.camera.position.y, last.camera.position.z];
}

/** Screen coordinates for the centre of the stage. */
const CENTRE = {
  clientX: HOST_RECT.left + HOST_RECT.width / 2,
  clientY: HOST_RECT.top + HOST_RECT.height / 2,
};

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('TopologyScene3D', () => {
  it('mounts the renderer surface into the stage', () => {
    renderScene();
    expect(host().querySelector('canvas')).not.toBeNull();
    expect(renders.length).toBeGreaterThan(0);
  });

  it('renders the camera controls, the gesture hint and the minimap', () => {
    renderScene();
    expect(screen.getByTestId('camera-controls-3d')).toBeInTheDocument();
    // Drag orbits here rather than panning, which is the one thing an operator
    // arriving from the plan will get wrong if nothing says so.
    expect(screen.getByTestId('scene3d-hint')).toHaveTextContent('orbit');
    expect(screen.getByTestId('minimap-panel-3d')).toBeInTheDocument();
  });

  it('hides the minimap on request', () => {
    renderScene({ showMinimap: false });
    expect(screen.queryByTestId('minimap-panel-3d')).not.toBeInTheDocument();
  });

  it('renders an empty stage for no topology without throwing', () => {
    renderScene({ topology: null });
    expect(screen.getByTestId('topology-scene3d')).toBeInTheDocument();
  });

  it('sizes the renderer to the stage, and again when the stage resizes', () => {
    renderScene();
    const before = renders.length;
    act(() =>
      resizeCallback?.(
        [{ contentRect: { width: 400, height: 300 } } as ResizeObserverEntry],
        {} as ResizeObserver,
      ),
    );
    runFrames(2);
    expect(renders.length).toBeGreaterThan(before);
  });

  it('ignores a resize to nothing rather than dividing by a zero viewport', () => {
    renderScene();
    expect(() =>
      act(() =>
        resizeCallback?.(
          [{ contentRect: { width: 0, height: 0 } } as ResizeObserverEntry],
          {} as ResizeObserver,
        ),
      ),
    ).not.toThrow();
  });

  it('disposes the renderer when it goes away', () => {
    const { unmount } = renderScene();
    unmount();
    expect(disposals).toBe(1);
  });

  // ── Navigation ──────────────────────────────────────────────────────────────

  it('orbits on drag and reports the drag on the stage', () => {
    renderScene();
    fireEvent.pointerDown(host(), { pointerId: 1, button: 0, ...CENTRE });
    expect(host()).toHaveAttribute('data-dragging', 'true');

    fireEvent.pointerMove(host(), {
      pointerId: 1,
      clientX: CENTRE.clientX + 120,
      clientY: CENTRE.clientY,
    });
    fireEvent.pointerUp(host(), {
      pointerId: 1,
      clientX: CENTRE.clientX + 120,
      clientY: CENTRE.clientY,
    });
    expect(host()).toHaveAttribute('data-dragging', 'false');
  });

  it('slides instead of orbiting when shift is held', () => {
    renderScene();
    fireEvent.pointerDown(host(), { pointerId: 1, button: 0, shiftKey: true, ...CENTRE });
    fireEvent.pointerMove(host(), {
      pointerId: 1,
      clientX: CENTRE.clientX + 60,
      clientY: CENTRE.clientY + 40,
    });
    fireEvent.pointerUp(host(), {
      pointerId: 1,
      clientX: CENTRE.clientX + 60,
      clientY: CENTRE.clientY + 40,
    });
    runFrames(2);
    expect(renders.length).toBeGreaterThan(0);
  });

  it('slides on a right-button drag, and swallows the context menu that follows', () => {
    renderScene();
    fireEvent.pointerDown(host(), { pointerId: 1, button: 2, ...CENTRE });
    fireEvent.pointerMove(host(), {
      pointerId: 1,
      clientX: CENTRE.clientX + 30,
      clientY: CENTRE.clientY,
    });
    fireEvent.pointerUp(host(), {
      pointerId: 1,
      clientX: CENTRE.clientX + 30,
      clientY: CENTRE.clientY,
    });
    const menu = fireEvent.contextMenu(host());
    expect(menu).toBe(false);
  });

  it('ends the drag when the pointer leaves the stage', () => {
    renderScene();
    fireEvent.pointerDown(host(), { pointerId: 1, button: 0, ...CENTRE });
    fireEvent.pointerLeave(host(), { pointerId: 1, ...CENTRE });
    expect(host()).toHaveAttribute('data-dragging', 'false');
    expect(host()).toHaveAttribute('data-hovering', 'false');
  });

  it('dollies on the wheel, and stops the page scrolling with it', () => {
    renderScene();
    const before = screen.getByTestId('zoom-display-3d').textContent;
    const event = new WheelEvent('wheel', { deltaY: 400, cancelable: true, bubbles: true });
    act(() => {
      host().dispatchEvent(event);
    });
    runFrames(3);
    expect(event.defaultPrevented).toBe(true);
    expect(screen.getByTestId('zoom-display-3d').textContent).not.toBe(before);
  });

  it('dollies from the camera buttons', () => {
    renderScene();
    const before = screen.getByTestId('zoom-display-3d').textContent;
    fireEvent.click(screen.getByLabelText('Zoom in'));
    runFrames(2);
    const zoomedIn = screen.getByTestId('zoom-display-3d').textContent;
    expect(zoomedIn).not.toBe(before);

    fireEvent.click(screen.getByLabelText('Zoom out'));
    runFrames(2);
    expect(screen.getByTestId('zoom-display-3d').textContent).not.toBe(zoomedIn);
  });

  it('keeps the camera where the operator put it when the stage resizes', () => {
    // The stage changes width whenever the inspector opens or the window is
    // dragged. Re-framing the estate at that moment throws away wherever the
    // operator had navigated to, mid-look.
    renderScene();
    fireEvent.click(screen.getByLabelText('Zoom in'));
    runFrames(3);
    const chosen = screen.getByTestId('zoom-display-3d').textContent;

    // Width only: the readout is world-units-per-pixel, which depends on the
    // viewport height, so changing the height would move the number for an
    // honest reason and prove nothing.
    act(() =>
      resizeCallback?.(
        [{ contentRect: { width: 640, height: HOST_RECT.height } } as ResizeObserverEntry],
        {} as ResizeObserver,
      ),
    );
    runFrames(3);
    expect(screen.getByTestId('zoom-display-3d').textContent).toBe(chosen);
  });

  it('takes a slow turn of its own once the operator leaves it alone', () => {
    // An estate seen from a dead-still camera reads as a diagram of something
    // that has stopped.
    renderScene();
    const before = renders.length;
    // Long past the idle delay, then a few frames of drift.
    runFrames(6, CAMERA3D.IDLE_DELAY_MS + 1000, 100);
    expect(renders.length).toBeGreaterThan(before);
    expect(eye()).not.toEqual([0, 0, 0]);

    const turned = eye();
    runFrames(4, CAMERA3D.IDLE_DELAY_MS + 2000, 100);
    expect(eye()).not.toEqual(turned);
  });

  it('holds the camera where it stands while the stage is paused', () => {
    // The counterpart of the drift test: same idle wait, same frames, and the
    // camera does not move — which is the whole of what the button promises.
    renderScene({ paused: true });
    runFrames(6, CAMERA3D.IDLE_DELAY_MS + 1000, 100);
    const held = eye();

    runFrames(4, CAMERA3D.IDLE_DELAY_MS + 2000, 100);
    expect(eye()).toEqual(held);
  });

  it('lets the camera go again when the pause is lifted', () => {
    const { rerender } = render(
      <TopologyScene3D topology={TOPOLOGY} createRenderer={fakeRendererFactory} paused />,
    );
    runFrames(4, CAMERA3D.IDLE_DELAY_MS + 1000, 100);
    const held = eye();

    rerender(<TopologyScene3D topology={TOPOLOGY} createRenderer={fakeRendererFactory} />);
    runFrames(4, CAMERA3D.IDLE_DELAY_MS + 2000, 100);
    expect(eye()).not.toEqual(held);
  });

  it('stops drifting the moment the operator touches it', () => {
    renderScene();
    runFrames(4, CAMERA3D.IDLE_DELAY_MS + 1000, 100);

    // Taking hold of it hands control back; the drift waits again.
    fireEvent.pointerDown(host(), { pointerId: 1, button: 0, ...CENTRE });
    fireEvent.pointerMove(host(), {
      pointerId: 1,
      clientX: CENTRE.clientX + 40,
      clientY: CENTRE.clientY,
    });
    fireEvent.pointerUp(host(), {
      pointerId: 1,
      clientX: CENTRE.clientX + 40,
      clientY: CENTRE.clientY,
    });
    const held = screen.getByTestId('zoom-display-3d').textContent;

    runFrames(2, CAMERA3D.IDLE_DELAY_MS + 1400, 16);
    // Drift turns the camera; it never changes how far back it stands.
    expect(screen.getByTestId('zoom-display-3d').textContent).toBe(held);
  });

  it('frames the whole estate again on reset', () => {
    renderScene();
    fireEvent.click(screen.getByLabelText('Zoom in'));
    runFrames(2);
    const zoomed = screen.getByTestId('zoom-display-3d').textContent;
    fireEvent.click(screen.getByTestId('camera-reset-3d'));
    runFrames(2);
    expect(screen.getByTestId('zoom-display-3d').textContent).not.toBe(zoomed);
  });

  it('turns on the arrows and slides on shift+arrows', () => {
    renderScene();
    for (const key of ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown']) {
      const turn = fireEvent.keyDown(host(), { key });
      expect(turn).toBe(false);
      const slide = fireEvent.keyDown(host(), { key, shiftKey: true });
      expect(slide).toBe(false);
    }
    runFrames(2);
    expect(renders.length).toBeGreaterThan(0);
  });

  it('dollies on + and −', () => {
    renderScene();
    const before = screen.getByTestId('zoom-display-3d').textContent;
    fireEvent.keyDown(host(), { key: '+' });
    runFrames(2);
    expect(screen.getByTestId('zoom-display-3d').textContent).not.toBe(before);
    fireEvent.keyDown(host(), { key: '-' });
    runFrames(2);
    fireEvent.keyDown(host(), { key: '=' });
    fireEvent.keyDown(host(), { key: '_' });
    runFrames(2);
  });

  it('leaves keys it does not own alone', () => {
    renderScene();
    expect(fireEvent.keyDown(host(), { key: 'a' })).toBe(true);
  });

  // ── Selection ───────────────────────────────────────────────────────────────

  it('reports a click on empty space as a cleared selection', () => {
    const onNodeClick = vi.fn();
    renderScene({ onNodeClick });
    // Top-left corner of the stage: the estate is framed centrally, so nothing
    // stands here.
    fireEvent.pointerDown(host(), { pointerId: 1, button: 0, clientX: 21, clientY: 11 });
    fireEvent.pointerUp(host(), { pointerId: 1, clientX: 21, clientY: 11 });
    expect(onNodeClick).toHaveBeenCalledWith(null);
  });

  it('treats a press that travelled as a camera move, not a choice', () => {
    const onNodeClick = vi.fn();
    renderScene({ onNodeClick });
    fireEvent.pointerDown(host(), { pointerId: 1, button: 0, ...CENTRE });
    fireEvent.pointerMove(host(), {
      pointerId: 1,
      clientX: CENTRE.clientX + 90,
      clientY: CENTRE.clientY + 30,
    });
    fireEvent.pointerUp(host(), {
      pointerId: 1,
      clientX: CENTRE.clientX + 90,
      clientY: CENTRE.clientY + 30,
    });
    expect(onNodeClick).not.toHaveBeenCalled();
  });

  it('selects what is under the cursor', () => {
    const onNodeClick = vi.fn();
    // One node, framed on its own: whatever the fit chooses, the thing in the
    // middle of the stage is unambiguous.
    const lone: Topology = {
      timestamp: TOPOLOGY.timestamp,
      nodes: [node('ravn-a', 'ravn_long')],
      edges: [],
    };
    renderScene({ topology: lone, onNodeClick });
    runFrames(4);
    fireEvent.pointerDown(host(), { pointerId: 1, button: 0, ...CENTRE });
    fireEvent.pointerUp(host(), { pointerId: 1, ...CENTRE });
    expect(onNodeClick).toHaveBeenCalledWith('ravn-a');
  });

  it('marks the stage when the cursor is over something', () => {
    const lone: Topology = {
      timestamp: TOPOLOGY.timestamp,
      nodes: [node('ravn-a', 'ravn_long')],
      edges: [],
    };
    renderScene({ topology: lone });
    runFrames(4);
    fireEvent.pointerMove(host(), { pointerId: 1, ...CENTRE });
    expect(host()).toHaveAttribute('data-hovering', 'true');
  });

  it('travels to a selection made from outside the stage', () => {
    const { rerender } = renderScene();
    const framed = screen.getByTestId('zoom-display-3d').textContent;

    rerender(
      <TopologyScene3D
        topology={TOPOLOGY}
        createRenderer={fakeRendererFactory}
        selectedId="ravn-a"
      />,
    );
    runFrames(60);
    // Selecting a resident in the rail is useless if it is off screen, so the
    // camera closes in on it.
    expect(screen.getByTestId('zoom-display-3d').textContent).not.toBe(framed);
  });

  it('honours an explicit focus that differs from the selection', () => {
    // Selecting a mesh is not a request to fly to whichever member happens to
    // be first, so focus is its own prop.
    const { rerender } = renderScene({ selectedId: 'ravn-a', focusId: null });
    const framed = screen.getByTestId('zoom-display-3d').textContent;
    runFrames(30);
    expect(screen.getByTestId('zoom-display-3d').textContent).toBe(framed);

    rerender(
      <TopologyScene3D
        topology={TOPOLOGY}
        createRenderer={fakeRendererFactory}
        selectedId="ravn-a"
        focusId="ravn-b"
      />,
    );
    runFrames(60);
    expect(screen.getByTestId('zoom-display-3d').textContent).not.toBe(framed);
  });

  it('ignores a focus on something the layout never placed', () => {
    expect(() => renderScene({ focusId: 'nowhere' })).not.toThrow();
  });

  it('clears the selection on Escape', () => {
    const onNodeClick = vi.fn();
    renderScene({ onNodeClick });
    fireEvent.keyDown(host(), { key: 'Escape' });
    expect(onNodeClick).toHaveBeenCalledWith(null);
  });

  // ── Filters ─────────────────────────────────────────────────────────────────

  it('hides a layer without moving anything', () => {
    // The decks must not rearrange when a connection is switched off.
    const { rerender } = renderScene();
    rerender(
      <TopologyScene3D
        topology={TOPOLOGY}
        createRenderer={fakeRendererFactory}
        hiddenLayers={new Set(['signals'])}
      />,
    );
    runFrames(2);
    expect(renders.length).toBeGreaterThan(0);
  });

  it('accepts a switched-off compute class', () => {
    renderScene({ hiddenCompute: new Set(['k8s']) });
    expect(screen.getByTestId('topology-scene3d')).toBeInTheDocument();
  });

  // ── Minimap ─────────────────────────────────────────────────────────────────

  it('travels when the minimap is clicked', () => {
    renderScene();
    const minimap = screen.getByLabelText('Topology minimap — click to travel');
    expect(() => fireEvent.click(minimap, { clientX: 10, clientY: 10 })).not.toThrow();
    runFrames(2);
  });

  // ── No WebGL ────────────────────────────────────────────────────────────────

  it('explains itself when the browser has no 3D context, instead of showing black', () => {
    // An empty black stage is indistinguishable from an estate with nothing in
    // it, which is the one thing the Observatory must never imply.
    render(<TopologyScene3D topology={TOPOLOGY} />);
    expect(screen.getByTestId('scene3d-fallback')).toBeInTheDocument();
    expect(screen.queryByTestId('camera-controls-3d')).not.toBeInTheDocument();
    expect(screen.queryByTestId('minimap-panel-3d')).not.toBeInTheDocument();
  });

  it('reports no WebGL when the probe cannot get a context', () => {
    expect(supportsWebGL()).toBe(false);
  });

  it('reports WebGL when the probe gets one', () => {
    HTMLCanvasElement.prototype.getContext = vi
      .fn()
      .mockImplementation((kind: string) => (kind === 'webgl2' ? {} : null)) as never;
    expect(supportsWebGL()).toBe(true);
  });

  it('reports no WebGL when asking for a context throws', () => {
    HTMLCanvasElement.prototype.getContext = (() => {
      throw new Error('context lost');
    }) as never;
    expect(supportsWebGL()).toBe(false);
  });
});
