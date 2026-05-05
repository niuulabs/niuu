import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

// Mock the vscode module — it only exists at runtime inside the VS Code
// extension host, and Vite cannot resolve it during tests.
vi.mock('vscode', () => ({
  Uri: {
    from: (c: Record<string, string>) => ({
      scheme: c.scheme,
      authority: c.authority,
      path: c.path,
    }),
  },
  workspace: {
    workspaceFolders: [],
    updateWorkspaceFolders: vi.fn(),
    openTextDocument: vi.fn().mockResolvedValue({ uri: {} }),
  },
  window: {
    tabGroups: { all: [], activeTabGroup: { activeTab: null } },
    showTextDocument: vi.fn().mockResolvedValue({}),
  },
  commands: {
    executeCommand: vi.fn().mockResolvedValue(undefined),
  },
}));

// Mock workbenchInit to prevent @codingame/monaco-vscode-* CSS imports
// from breaking the test environment (Node.js can't handle .css files).
vi.mock('@/components/EditorPanel/workbenchInit', () => ({
  initWorkbench: vi.fn().mockResolvedValue(undefined),
  switchSession: vi.fn().mockResolvedValue(undefined),
}));

// Polyfill scrollIntoView — jsdom does not implement it
if (typeof Element.prototype.scrollIntoView === 'undefined') {
  Element.prototype.scrollIntoView = vi.fn();
}

// Polyfill ResizeObserver for assistant-ui components that use it
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class ResizeObserver {
    _callback: ResizeObserverCallback;
    constructor(callback: ResizeObserverCallback) {
      this._callback = callback;
    }
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof globalThis.ResizeObserver;
}

// Polyfill localStorage — jsdom may provide a broken Storage in some contexts
if (
  typeof globalThis.localStorage === 'undefined' ||
  typeof globalThis.localStorage.getItem !== 'function'
) {
  const store = new Map<string, string>();
  globalThis.localStorage = {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
    clear: () => {
      store.clear();
    },
    get length() {
      return store.size;
    },
    key: (i: number) => Array.from(store.keys())[i] ?? null,
  };
}

// Cleanup after each test
afterEach(() => {
  cleanup();
});
