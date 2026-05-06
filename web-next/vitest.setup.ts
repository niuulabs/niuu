import '@testing-library/jest-dom/vitest';
import { vi } from 'vitest';

// Node 22+ ships a built-in `localStorage` global that requires a valid
// `--localstorage-file` path. Without it the object exists but methods like
// `clear()`, `getItem()`, etc. are missing — breaking every test that touches
// localStorage. Override with a simple in-memory implementation so jsdom tests
// behave as expected regardless of the Node version.
if (typeof localStorage === 'undefined' || typeof localStorage.clear !== 'function') {
  const store = new Map<string, string>();
  const storage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => store.set(key, String(value)),
    removeItem: (key: string) => store.delete(key),
    clear: () => store.clear(),
    get length() {
      return store.size;
    },
    key: (index: number) => [...store.keys()][index] ?? null,
  };
  Object.defineProperty(globalThis, 'localStorage', {
    value: storage,
    writable: true,
    configurable: true,
  });
}

// TanStack Router calls window.scrollTo during scroll-restoration inside tests.
// jsdom doesn't implement it; stub it out to keep test output clean.
Object.defineProperty(window, 'scrollTo', { value: () => {}, writable: true });

// ResizeObserver is not available in jsdom — used by SessionChat scroll tracking
// and Radix UI primitives (Tooltip/Popover arrow sizing).
global.ResizeObserver = vi.fn().mockImplementation(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
}));

// scrollIntoView is not implemented in jsdom — used by SlashCommandMenu,
// SessionChat, and Radix UI focus-jump flows.
Element.prototype.scrollIntoView = vi.fn();

// jsdom doesn't implement PointerEvent methods used by Radix UI primitives.
if (!Element.prototype.hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false;
}
if (!Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = () => {};
}
if (!Element.prototype.releasePointerCapture) {
  Element.prototype.releasePointerCapture = () => {};
}

// jsdom doesn't implement HTMLCanvasElement.prototype.getContext.
// Return a minimal stub so canvas-based components (e.g. AmbientTopology,
// RaidMeshCanvas) don't print "Not implemented" warnings during tests.
if (typeof HTMLCanvasElement !== 'undefined') {
  HTMLCanvasElement.prototype.getContext = () =>
    ({
      clearRect: () => {},
      beginPath: () => {},
      moveTo: () => {},
      lineTo: () => {},
      arc: () => {},
      fill: () => {},
      stroke: () => {},
      fillText: () => {},
      setTransform: () => {},
      createRadialGradient: () => ({
        addColorStop: () => {},
      }),
      strokeStyle: '',
      lineWidth: 0,
      fillStyle: '',
      font: '',
      textAlign: 'start' as CanvasTextAlign,
    }) as unknown as CanvasRenderingContext2D;
}

// matchMedia is not implemented in jsdom; provide a silent stub so components
// that call window.matchMedia() (e.g. useReducedMotion) don't throw.
if (typeof window.matchMedia === 'undefined') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: () => ({
      matches: false,
      addEventListener: () => {},
      removeEventListener: () => {},
    }),
  });
}
