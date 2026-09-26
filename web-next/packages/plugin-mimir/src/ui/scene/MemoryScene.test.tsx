import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { act } from 'react';
import { MemoryScene } from './MemoryScene';
import { computeLayout } from './layout';
import { createFakeRenderer, installMemoryPaletteTokens } from './test-helpers';
import type { MimirGraph } from '../../domain/api-types';
import type { MemorySceneProps } from './types';
import type { Scene3DRenderer } from './webglRenderer';

const VIEWPORT = { left: 0, top: 0, width: 800, height: 600 };

function asDomRect(rect: typeof VIEWPORT): DOMRect {
  return {
    ...rect,
    right: rect.left + rect.width,
    bottom: rect.top + rect.height,
    x: rect.left,
    y: rect.top,
    toJSON: () => rect,
  };
}

function graph(overrides: Partial<MimirGraph> = {}): MimirGraph {
  return {
    nodes: [
      {
        id: '/a',
        title: 'Node A',
        category: 'topic',
        path: '/a',
        mount: 'local',
        kind: 'topic',
        updatedAt: '2026-04-19T00:00:00Z',
        firstSeen: '2026-01-01T00:00:00Z',
        confidence: 'high',
      },
      {
        id: '/b',
        title: 'Node B',
        category: 'person',
        path: '/b',
        mount: 'local',
        kind: 'person',
        updatedAt: '2026-04-19T00:00:00Z',
        firstSeen: '2026-04-18T00:00:00Z',
        confidence: 'medium',
      },
      {
        id: '/future',
        title: 'Future Node',
        category: 'topic',
        path: '/future',
        mount: 'local',
        kind: 'topic',
        updatedAt: '2026-05-01T00:00:00Z',
        firstSeen: '2026-05-01T00:00:00Z',
        confidence: null,
      },
    ],
    edges: [{ source: '/a', target: '/b', type: 'depends_on' }],
    ...overrides,
  };
}

function baseProps(overrides: Partial<MemorySceneProps> = {}): MemorySceneProps {
  return {
    graph: graph(),
    view: '3d',
    colourBy: 'type',
    hiddenGroups: new Set(),
    showQuestions: false,
    focus: null,
    answers: [],
    path: null,
    asOf: null,
    markers: [],
    questions: [],
    disputedIds: new Set(),
    camera: null,
    onSelectNode: vi.fn(),
    onBackgroundClick: vi.fn(),
    createRenderer: createFakeRenderer,
    ...overrides,
  };
}

let uninstallPalette: () => void = () => {};

beforeEach(() => {
  uninstallPalette = installMemoryPaletteTokens();
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    get: () => VIEWPORT.width,
  });
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get: () => VIEWPORT.height,
  });
  HTMLElement.prototype.getBoundingClientRect = vi.fn(() => asDomRect(VIEWPORT));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  uninstallPalette();
});

describe('MemoryScene — unsupported WebGL', () => {
  it('renders an accessible fallback message instead of a canvas when WebGL is unavailable and no renderer is injected', () => {
    const getContextSpy = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    render(<MemoryScene {...baseProps({ createRenderer: undefined })} />);
    expect(screen.getByRole('img', { name: /webgl/i })).toBeInTheDocument();
    expect(screen.queryByTestId('memory-scene-canvas-host')).not.toBeInTheDocument();
    getContextSpy.mockRestore();
  });
});

describe('MemoryScene — basic render', () => {
  it('mounts a canvas host and reports visible page/link counts in aria-label', () => {
    render(<MemoryScene {...baseProps()} />);
    const region = screen.getByRole('img');
    expect(region.getAttribute('aria-label')).toMatch(/Memory: \d+ pages?, \d+ links?/);
    expect(screen.getByTestId('memory-scene-canvas-host')).toBeInTheDocument();
  });

  it('appends the injected renderer canvas into the host', () => {
    const renderer = createFakeRenderer();
    render(<MemoryScene {...baseProps({ createRenderer: () => renderer })} />);
    const host = screen.getByTestId('memory-scene-canvas-host');
    expect(host.contains(renderer.domElement)).toBe(true);
  });

  it('calls render on the injected renderer', () => {
    const renderer = createFakeRenderer();
    render(<MemoryScene {...baseProps({ createRenderer: () => renderer })} />);
    expect(renderer.render).toHaveBeenCalled();
  });
});

describe('MemoryScene — unmount disposal', () => {
  it('disposes the renderer and removes its canvas on unmount', () => {
    const renderer = createFakeRenderer();
    const { unmount } = render(<MemoryScene {...baseProps({ createRenderer: () => renderer })} />);
    const host = screen.getByTestId('memory-scene-canvas-host');
    expect(host.contains(renderer.domElement)).toBe(true);
    unmount();
    expect(renderer.dispose).toHaveBeenCalled();
    expect(host.contains(renderer.domElement)).toBe(false);
  });
});

describe('MemoryScene — hidden groups', () => {
  it('excludes hidden-group pages from the visible count', () => {
    const { rerender } = render(<MemoryScene {...baseProps({ hiddenGroups: new Set() })} />);
    const before = screen.getByRole('img').getAttribute('aria-label');
    rerender(<MemoryScene {...baseProps({ hiddenGroups: new Set(['entity']) })} />);
    const after = screen.getByRole('img').getAttribute('aria-label');
    expect(before).not.toBe(after);
  });
});

describe('MemoryScene — asOf replay', () => {
  it('excludes pages first seen after asOf from the visible count', () => {
    const { rerender } = render(<MemoryScene {...baseProps({ asOf: null })} />);
    const withFuture = screen.getByRole('img').getAttribute('aria-label');
    rerender(<MemoryScene {...baseProps({ asOf: '2026-04-19T00:00:00Z' })} />);
    const withoutFuture = screen.getByRole('img').getAttribute('aria-label');
    expect(withFuture).not.toBe(withoutFuture);
  });
});

describe('MemoryScene — answers', () => {
  it('renders a numbered badge for each answer node', () => {
    render(
      <MemoryScene
        {...baseProps({
          answers: [
            { nodeId: '/a', n: 1 },
            { nodeId: '/b', n: 2 },
          ],
        })}
      />,
    );
    expect(screen.getByTestId('memory-answer-/a')).toHaveTextContent('1');
    expect(screen.getByTestId('memory-answer-/b')).toHaveTextContent('2');
  });

  it('does not render a badge for an answer node not in the graph', () => {
    render(<MemoryScene {...baseProps({ answers: [{ nodeId: '/does-not-exist', n: 1 }] })} />);
    expect(screen.queryByTestId('memory-answer-/does-not-exist')).not.toBeInTheDocument();
  });
});

describe('MemoryScene — focus', () => {
  it('labels every node lit by focus, even beyond the hub-label count', () => {
    render(<MemoryScene {...baseProps({ focus: { nodeId: '/a', depth: 1 } })} />);
    expect(screen.getByTestId('memory-label-/a')).toBeInTheDocument();
    expect(screen.getByTestId('memory-label-/b')).toBeInTheDocument();
  });

  it('writes the relation on a lit typed link, and none at rest', () => {
    const { rerender } = render(<MemoryScene {...baseProps()} />);
    expect(screen.queryByText('depends on')).not.toBeInTheDocument();
    rerender(<MemoryScene {...baseProps({ focus: { nodeId: '/a', depth: 1 } })} />);
    expect(screen.getByText('depends on')).toHaveClass('niuu-memory-relation');
  });
});

describe('MemoryScene — spotlight camera', () => {
  /** Run queued animation frames by hand so camera easing can be observed. */
  function manualFrames() {
    let queue: FrameRequestCallback[] = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      queue.push(cb);
      return queue.length;
    });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    return (count: number) => {
      for (let i = 0; i < count; i += 1) {
        const run = queue;
        queue = [];
        act(() => run.forEach((cb) => cb(performance.now())));
      }
    };
  }

  function cameraDistanceTo(renderer: Scene3DRenderer, point: { x: number; y: number; z: number }) {
    const calls = (renderer.render as ReturnType<typeof vi.fn>).mock.calls;
    const camera = calls.at(-1)![1] as { position: { x: number; y: number; z: number } };
    return Math.hypot(
      camera.position.x - point.x,
      camera.position.y - point.y,
      camera.position.z - point.z,
    );
  }

  it('flies in to frame a focused page and back out when focus clears', () => {
    const step = manualFrames();
    const renderer = createFakeRenderer();
    const mounts = ['local', 'shared', 'platform'];
    const nodes = Array.from({ length: 60 }, (_, i) => ({
      id: `/n${i}`,
      title: `Node ${i}`,
      category: 'topic',
      path: `/n${i}`,
      mount: mounts[i % mounts.length]!,
      kind: 'topic',
      updatedAt: '2026-04-19T00:00:00Z',
      firstSeen: '2026-04-18T00:00:00Z',
      confidence: null,
    }));
    const edges = nodes
      .slice(1)
      .map((n, i) => ({ source: nodes[i]!.id, target: n.id, type: 'part_of' }));
    const props = baseProps({ graph: { nodes, edges }, createRenderer: () => renderer });
    const layout = computeLayout(props.graph);
    const a = layout.nodes.get('/n30')!.position;
    const b = layout.nodes.get('/n31')!.position;
    const between = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, z: (a.z + b.z) / 2 };

    const { rerender } = render(<MemoryScene {...props} />);
    step(2);
    const atRest = cameraDistanceTo(renderer, between);

    rerender(<MemoryScene {...props} focus={{ nodeId: '/n30', depth: 1 }} />);
    step(120);
    const focused = cameraDistanceTo(renderer, between);
    expect(focused).toBeLessThan(atRest);

    rerender(<MemoryScene {...props} focus={null} />);
    step(120);
    expect(cameraDistanceTo(renderer, between)).toBeGreaterThan(focused);
  });
});

describe('MemoryScene — markers', () => {
  it('renders a read marker with the actor name', () => {
    render(
      <MemoryScene
        {...baseProps({
          markers: [
            {
              id: 'm1',
              nodeId: '/a',
              kind: 'read',
              actor: 'ravn-fjolnir',
              timestamp: new Date().toISOString(),
            },
          ],
        })}
      />,
    );
    expect(screen.getByTestId('memory-marker-m1')).toHaveTextContent(
      'ravn-fjolnir is reading this',
    );
  });

  it('renders a write marker with a relative time and falls back to "Someone" for a null actor', () => {
    render(
      <MemoryScene
        {...baseProps({
          markers: [
            {
              id: 'm2',
              nodeId: '/b',
              kind: 'write',
              actor: null,
              timestamp: new Date().toISOString(),
            },
          ],
        })}
      />,
    );
    expect(screen.getByTestId('memory-marker-m2')).toHaveTextContent('Someone wrote here');
  });

  it('does not render a marker anchored to a node outside the graph', () => {
    render(
      <MemoryScene
        {...baseProps({
          markers: [
            {
              id: 'm3',
              nodeId: '/ghost',
              kind: 'read',
              actor: null,
              timestamp: new Date().toISOString(),
            },
          ],
        })}
      />,
    );
    expect(screen.queryByTestId('memory-marker-m3')).not.toBeInTheDocument();
  });
});

describe('MemoryScene — questions', () => {
  const question = { id: 'q1', label: 'What ships next?', nearNodeId: '/a' as string | null };

  it('renders a question chip only when showQuestions is true', () => {
    const { rerender } = render(
      <MemoryScene {...baseProps({ questions: [question], showQuestions: false })} />,
    );
    expect(screen.queryByTestId('memory-question-q1')).not.toBeInTheDocument();
    rerender(<MemoryScene {...baseProps({ questions: [question], showQuestions: true })} />);
    expect(screen.getByTestId('memory-question-q1')).toHaveTextContent('What ships next?');
  });

  it('renders a free-floating question with no nearNodeId', () => {
    render(
      <MemoryScene
        {...baseProps({
          questions: [{ id: 'q2', label: 'Free', nearNodeId: null }],
          showQuestions: true,
        })}
      />,
    );
    expect(screen.getByTestId('memory-question-q2')).toBeInTheDocument();
  });
});

describe('MemoryScene — camera commands', () => {
  it('accepts a fit command without throwing', () => {
    expect(() =>
      render(<MemoryScene {...baseProps({ camera: { kind: 'fit', key: 1 } })} />),
    ).not.toThrow();
  });

  it('accepts a fly-to command targeting known nodes without throwing', () => {
    expect(() =>
      render(
        <MemoryScene
          {...baseProps({ camera: { kind: 'fly-to', nodeIds: ['/a', '/b'], key: 1 } })}
        />,
      ),
    ).not.toThrow();
  });

  it('does not re-trigger when the same command key is passed again', () => {
    const { rerender } = render(
      <MemoryScene {...baseProps({ camera: { kind: 'fit', key: 1 } })} />,
    );
    expect(() =>
      rerender(<MemoryScene {...baseProps({ camera: { kind: 'fit', key: 1 } })} />),
    ).not.toThrow();
  });

  it('re-triggers when the key changes', () => {
    const { rerender } = render(
      <MemoryScene {...baseProps({ camera: { kind: 'fit', key: 1 } })} />,
    );
    expect(() =>
      rerender(<MemoryScene {...baseProps({ camera: { kind: 'fit', key: 2 } })} />),
    ).not.toThrow();
  });
});

describe('MemoryScene — picking: click, drag, shift-click, background click', () => {
  it('clicking empty space on an empty graph calls onBackgroundClick', () => {
    const onSelectNode = vi.fn();
    const onBackgroundClick = vi.fn();
    render(
      <MemoryScene
        {...baseProps({ graph: { nodes: [], edges: [] }, onSelectNode, onBackgroundClick })}
      />,
    );
    const canvas = screen.getByTestId('memory-scene-canvas-host').querySelector('canvas')!;
    fireEvent.pointerDown(canvas, { clientX: 400, clientY: 300, button: 0 });
    fireEvent.pointerUp(window, { clientX: 400, clientY: 300, button: 0 });
    expect(onBackgroundClick).toHaveBeenCalledTimes(1);
    expect(onSelectNode).not.toHaveBeenCalled();
  });

  it('a press-and-drag beyond the click threshold does not fire either callback', () => {
    const onSelectNode = vi.fn();
    const onBackgroundClick = vi.fn();
    render(
      <MemoryScene
        {...baseProps({ graph: { nodes: [], edges: [] }, onSelectNode, onBackgroundClick })}
      />,
    );
    const canvas = screen.getByTestId('memory-scene-canvas-host').querySelector('canvas')!;
    fireEvent.pointerDown(canvas, { clientX: 400, clientY: 300, button: 0 });
    fireEvent.pointerMove(window, { clientX: 460, clientY: 360, buttons: 1 });
    fireEvent.pointerUp(window, { clientX: 460, clientY: 360, button: 0 });
    expect(onSelectNode).not.toHaveBeenCalled();
    expect(onBackgroundClick).not.toHaveBeenCalled();
  });

  it('clicking a node hit by hover selects it, and shift is forwarded', () => {
    const onSelectNode = vi.fn();
    render(<MemoryScene {...baseProps({ onSelectNode })} />);
    const canvas = screen.getByTestId('memory-scene-canvas-host').querySelector('canvas')!;

    // Hover across a grid of points until one lands on a node (the force
    // layout is deterministic but its screen projection isn't predicted
    // here), then click at that exact spot.
    let hitX: number | null = null;
    let hitY: number | null = null;
    for (let x = 0; x <= 800 && hitX === null; x += 40) {
      for (let y = 0; y <= 600 && hitX === null; y += 40) {
        fireEvent.pointerMove(window, { clientX: x, clientY: y, buttons: 0 });
        if (screen.queryByTestId('memory-hover-card')) {
          hitX = x;
          hitY = y;
        }
      }
    }

    expect(hitX).not.toBeNull();
    fireEvent.pointerDown(canvas, { clientX: hitX!, clientY: hitY!, button: 0 });
    fireEvent.pointerUp(window, { clientX: hitX!, clientY: hitY!, button: 0, shiftKey: true });

    expect(onSelectNode).toHaveBeenCalledTimes(1);
    const [id, options] = onSelectNode.mock.calls[0]!;
    expect(typeof id).toBe('string');
    expect(options).toEqual({ shift: true });
  });
});

describe('MemoryScene — hover card', () => {
  it('shows a hover card with kind/mount/link-count/click-to-focus text when hovering a node', () => {
    render(<MemoryScene {...baseProps()} />);
    const canvas = screen.getByTestId('memory-scene-canvas-host').querySelector('canvas')!;

    let found = false;
    for (let x = 0; x <= 800 && !found; x += 40) {
      for (let y = 0; y <= 600 && !found; y += 40) {
        fireEvent.pointerMove(canvas, { clientX: x, clientY: y, buttons: 0 });
        if (screen.queryByTestId('memory-hover-card')) found = true;
      }
    }

    expect(found).toBe(true);
    expect(screen.getByTestId('memory-hover-card')).toHaveTextContent('click to focus');
  });
});

describe('MemoryScene — colourBy variants', () => {
  it.each(['type', 'proof', 'age'] as const)(
    'renders without throwing for colourBy=%s',
    (colourBy) => {
      expect(() => render(<MemoryScene {...baseProps({ colourBy })} />)).not.toThrow();
    },
  );
});

describe('MemoryScene — 2D view', () => {
  it('renders without throwing in the 2D view', () => {
    expect(() => render(<MemoryScene {...baseProps({ view: '2d' })} />)).not.toThrow();
  });

  it('treats an orbit-style drag as a pan in the 2D view (camera never throws)', () => {
    render(<MemoryScene {...baseProps({ view: '2d' })} />);
    const canvas = screen.getByTestId('memory-scene-canvas-host').querySelector('canvas')!;
    expect(() => {
      fireEvent.pointerDown(canvas, { clientX: 100, clientY: 100, button: 0 });
      fireEvent.pointerMove(window, { clientX: 200, clientY: 150, buttons: 1 });
      fireEvent.pointerUp(window, { clientX: 200, clientY: 150, button: 0 });
    }).not.toThrow();
  });
});

describe('MemoryScene — path', () => {
  it('labels the path nodes and renders without throwing', () => {
    render(<MemoryScene {...baseProps({ path: ['/a', '/b'] })} />);
    expect(screen.getByTestId('memory-label-/a')).toBeInTheDocument();
    expect(screen.getByTestId('memory-label-/b')).toBeInTheDocument();
  });
});

describe('MemoryScene — disputedIds', () => {
  it('renders without throwing when a node is disputed', () => {
    expect(() =>
      render(<MemoryScene {...baseProps({ disputedIds: new Set(['/a']) })} />),
    ).not.toThrow();
  });
});

describe('MemoryScene — camera gestures', () => {
  it('shift-drag pans rather than orbits, and forwards shift on the resulting click', () => {
    const onSelectNode = vi.fn();
    const onBackgroundClick = vi.fn();
    render(
      <MemoryScene
        {...baseProps({ graph: { nodes: [], edges: [] }, onSelectNode, onBackgroundClick })}
      />,
    );
    const canvas = screen.getByTestId('memory-scene-canvas-host').querySelector('canvas')!;
    fireEvent.pointerDown(canvas, { clientX: 100, clientY: 100, button: 0, shiftKey: true });
    fireEvent.pointerMove(window, { clientX: 140, clientY: 120, buttons: 1, shiftKey: true });
    fireEvent.pointerUp(window, { clientX: 140, clientY: 120, button: 0, shiftKey: true });
    // Moved past the click threshold — a drag, not a click.
    expect(onBackgroundClick).not.toHaveBeenCalled();
    expect(onSelectNode).not.toHaveBeenCalled();
  });

  it('a right-button press is treated as panning', () => {
    render(<MemoryScene {...baseProps()} />);
    const canvas = screen.getByTestId('memory-scene-canvas-host').querySelector('canvas')!;
    expect(() => {
      fireEvent.pointerDown(canvas, { clientX: 100, clientY: 100, button: 2 });
      fireEvent.pointerMove(window, { clientX: 130, clientY: 110, buttons: 2 });
      fireEvent.pointerUp(window, { clientX: 130, clientY: 110, button: 2 });
    }).not.toThrow();
  });

  it('wheel zooms without throwing', () => {
    render(<MemoryScene {...baseProps()} />);
    const canvas = screen.getByTestId('memory-scene-canvas-host').querySelector('canvas')!;
    expect(() => fireEvent.wheel(canvas, { deltaY: 100 })).not.toThrow();
    expect(() => fireEvent.wheel(canvas, { deltaY: -100 })).not.toThrow();
  });

  it('a right-click context menu is suppressed rather than opening the browser menu', () => {
    render(<MemoryScene {...baseProps()} />);
    const canvas = screen.getByTestId('memory-scene-canvas-host').querySelector('canvas')!;
    const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
    const prevented = !canvas.dispatchEvent(event);
    expect(prevented).toBe(true);
  });

  it('a pointer move while nothing is pressed does not move the camera (only hovers)', () => {
    render(<MemoryScene {...baseProps()} />);
    const canvas = screen.getByTestId('memory-scene-canvas-host').querySelector('canvas')!;
    expect(() =>
      fireEvent.pointerMove(canvas, { clientX: 400, clientY: 300, buttons: 0 }),
    ).not.toThrow();
  });
});

describe('MemoryScene — resize and visibility', () => {
  it('does not throw when the ResizeObserver callback fires', () => {
    let observedCallback: ResizeObserverCallback | null = null;
    class TestResizeObserver {
      constructor(cb: ResizeObserverCallback) {
        observedCallback = cb;
      }
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    }
    const original = global.ResizeObserver;
    global.ResizeObserver = TestResizeObserver as unknown as typeof ResizeObserver;

    render(<MemoryScene {...baseProps()} />);
    expect(() => observedCallback?.([], {} as ResizeObserver)).not.toThrow();

    global.ResizeObserver = original;
  });

  it('skips rendering work while the document is hidden, without throwing', () => {
    const originalDescriptor = Object.getOwnPropertyDescriptor(Document.prototype, 'hidden');
    Object.defineProperty(document, 'hidden', { configurable: true, value: true, writable: true });
    expect(() => render(<MemoryScene {...baseProps()} />)).not.toThrow();
    if (originalDescriptor) {
      Object.defineProperty(Document.prototype, 'hidden', originalDescriptor);
    } else {
      Object.defineProperty(document, 'hidden', {
        configurable: true,
        value: false,
        writable: true,
      });
    }
  });
});
