import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import {
  deriveHttpBase,
  killSession,
  listSessions,
  SessionTerminalLive,
  spawnSession,
} from './SessionTerminalLive';
import { useWebSocket } from './hooks/useWebSocket';

vi.mock('@niuulabs/query', () => ({
  getAccessToken: vi.fn(() => 'token-123'),
  getAuthHeaders: vi.fn((headers?: HeadersInit) => {
    const next = new Headers(headers);
    next.set('Authorization', 'Bearer token-123');
    return next;
  }),
}));

vi.mock('./hooks/useWebSocket', () => ({
  useWebSocket: vi.fn(() => ({ sendJson: vi.fn() })),
}));

const mockXtermOpen = vi.fn();
const mockXtermWrite = vi.fn();
const mockXtermDispose = vi.fn();
const mockXtermLoadAddon = vi.fn();
const mockXtermOnData = vi.fn();
const mockXtermOnResize = vi.fn();
const mockFitAddonFit = vi.fn();
const mockXtermFocus = vi.fn();
const mockXtermRefresh = vi.fn();

const onDataCallbacks: Array<(data: string) => void> = [];
const onResizeCallbacks: Array<(event: { cols: number; rows: number }) => void> = [];
const xtermInstances: Array<{ options?: unknown }> = [];
const resizeObservers: ResizeObserverStub[] = [];
let latestWebSocketUrl: string | null = null;
let latestWebSocketOptions:
  | {
      snapshotHandlersPerConnection?: boolean;
      onOpen?: () => void;
      onMessage?: (raw: string) => void;
      onClose?: () => void;
      onError?: () => void;
    }
  | undefined;
const mockSendJson = vi.fn();

vi.mock('@xterm/xterm', () => ({
  Terminal: class MockXtermTerminal {
    open = mockXtermOpen;
    write = mockXtermWrite;
    dispose = mockXtermDispose;
    loadAddon = mockXtermLoadAddon;
    focus = mockXtermFocus;
    refresh = mockXtermRefresh;
    onData = vi.fn((callback: (data: string) => void) => {
      onDataCallbacks.push(callback);
      const disposable = { dispose: vi.fn() };
      mockXtermOnData(callback);
      return disposable;
    });
    onResize = vi.fn((callback: (event: { cols: number; rows: number }) => void) => {
      onResizeCallbacks.push(callback);
      const disposable = { dispose: vi.fn() };
      mockXtermOnResize(callback);
      return disposable;
    });
    cols = 80;
    rows = 24;

    constructor(options?: unknown) {
      xtermInstances.push(this);
      this.options = options;
    }

    options?: unknown;
  },
}));

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class MockFitAddon {
    fit = mockFitAddonFit;
  },
}));

vi.mock('@xterm/addon-web-links', () => ({
  WebLinksAddon: class MockWebLinksAddon {},
}));

class ResizeObserverStub {
  callback: ResizeObserverCallback;
  observe = vi.fn();
  disconnect = vi.fn();
  unobserve = vi.fn();

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    resizeObservers.push(this);
  }
}

vi.stubGlobal('ResizeObserver', ResizeObserverStub);

const useWebSocketMock = vi.mocked(useWebSocket);

function setDocumentFonts(value: Document['fonts'] | undefined) {
  Object.defineProperty(document, 'fonts', {
    configurable: true,
    writable: true,
    value,
  });
}

/**
 * Wait until the component has opened its websocket.
 *
 * Every driver below reaches for `latestWebSocketOptions?.`, so if the effect
 * that registers it has not run yet the call silently does nothing and the
 * assertion after it fails. Rendered output does not imply that effect has
 * run — they are separate effects — which made these tests fail
 * intermittently under CI load.
 */
async function waitForSocket(): Promise<void> {
  await waitFor(() => expect(latestWebSocketOptions).toBeDefined());
}

/** Wait for the websocket *and* the attached terminal, for tests driving both. */
async function waitForTerminalWiring(): Promise<void> {
  await waitForSocket();
  await waitFor(() => expect(xtermInstances.length).toBeGreaterThan(0));
}

describe('SessionTerminalLive helpers', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    global.fetch = vi.fn();
    vi.clearAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('derives the HTTP base from websocket URLs', () => {
    expect(deriveHttpBase('ws://localhost:8080/ws')).toBe('http://localhost:8080');
    expect(deriveHttpBase('wss://example.com/prefix/ws')).toBe('https://example.com/prefix');
  });

  it('lists sessions with auth headers and handles missing/failed backends', async () => {
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(new Response(null, { status: 404 }))
      .mockResolvedValueOnce(new Response(null, { status: 500 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sessions: [
              { terminalId: 'term-1', label: 'Main', cli_type: 'shell', status: 'running' },
            ],
          }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      );

    await expect(listSessions('https://example.com')).resolves.toBeNull();
    await expect(listSessions('https://example.com')).resolves.toEqual([]);
    await expect(listSessions('https://example.com')).resolves.toEqual([
      { terminalId: 'term-1', label: 'Main', cli_type: 'shell', status: 'running' },
    ]);

    expect(global.fetch).toHaveBeenLastCalledWith('https://example.com/api/terminal/sessions', {
      headers: { authorization: 'Bearer token-123' },
    });
  });

  it('spawns a terminal session with the selected CLI type', async () => {
    vi.mocked(global.fetch).mockResolvedValue(
      new Response(JSON.stringify({ terminalId: 'term-2', label: 'Shell 2' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    await expect(spawnSession('https://example.com', 'shell')).resolves.toEqual({
      terminalId: 'term-2',
      label: 'Shell 2',
    });

    expect(global.fetch).toHaveBeenCalledWith('https://example.com/api/terminal/spawn', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: 'Bearer token-123',
      },
      body: JSON.stringify({ cli_type: 'shell' }),
    });
  });

  it('falls back to the terminal id when spawn response omits a label', async () => {
    vi.mocked(global.fetch).mockResolvedValue(
      new Response(JSON.stringify({ terminalId: 'term-3' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    await expect(spawnSession('https://example.com', 'zsh')).resolves.toEqual({
      terminalId: 'term-3',
      label: 'term-3',
    });
  });

  it('returns null when spawn fails', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 500 }));

    await expect(spawnSession('https://example.com', 'shell')).resolves.toBeNull();
  });

  it('kills a terminal session with auth headers', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 204 }));

    await killSession('https://example.com', 'term-9');

    expect(global.fetch).toHaveBeenCalledWith('https://example.com/api/terminal/kill', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: 'Bearer token-123',
      },
      body: JSON.stringify({ terminalId: 'term-9' }),
    });
  });

  it('swallows kill-session fetch errors', async () => {
    vi.mocked(global.fetch).mockRejectedValue(new Error('network down'));

    await expect(killSession('https://example.com', 'term-9')).resolves.toBeUndefined();
  });
});

describe('SessionTerminalLive', () => {
  const originalFetch = global.fetch;
  const originalFonts = document.fonts;

  beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 404 }));
    vi.clearAllMocks();
    xtermInstances.length = 0;
    onDataCallbacks.length = 0;
    onResizeCallbacks.length = 0;
    resizeObservers.length = 0;
    latestWebSocketUrl = null;
    latestWebSocketOptions = undefined;
    mockSendJson.mockReset();
    useWebSocketMock.mockImplementation((url, options) => {
      latestWebSocketUrl = url;
      latestWebSocketOptions = options;
      return { sendJson: mockSendJson };
    });
    setDocumentFonts(undefined);
  });

  afterEach(() => {
    global.fetch = originalFetch;
    setDocumentFonts(originalFonts);
    vi.useRealTimers();
  });

  it('renders a fallback when no websocket URL is available', () => {
    render(<SessionTerminalLive url={null} />);
    expect(screen.getByText('terminal unavailable')).toBeInTheDocument();
  });

  it('renders the legacy-transport notice when the backend does not expose terminal sessions', async () => {
    render(<SessionTerminalLive url="ws://localhost:8080/ws" />);
    await waitFor(() =>
      expect(
        screen.getByText('This backend does not expose the legacy terminal transport yet.'),
      ).toBeInTheDocument(),
    );
  });

  it('restores existing terminal tabs from the backend', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessions: [
            { terminalId: 'term-1', label: 'Tab 1', cli_type: 'shell', status: 'running' },
            { terminalId: 'term-2', label: 'Tab 2', cli_type: 'claude', status: 'running' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(screen.getByRole('tablist')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole('tab', { name: /tab 1/i })).toBeInTheDocument());
    expect(screen.getByRole('tab', { name: /tab 2/i })).toBeInTheDocument();
    expect(latestWebSocketUrl).toBe('ws://localhost:8080/terminal/ws/term-1');
  });

  it('adds a new CLI tab from the menu', async () => {
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sessions: [
              { terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ terminalId: 'term-2', label: 'Claude' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      );

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(screen.getByRole('tab', { name: /shell 1/i })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /new terminal/i }));
    fireEvent.click(screen.getByRole('menuitem', { name: /claude/i }));

    await waitFor(() => expect(screen.getByRole('tab', { name: /claude/i })).toBeInTheDocument());
  });

  it('offers a shell tab option in the new terminal menu', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessions: [
            { terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(screen.getByRole('tab', { name: /shell 1/i })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /new terminal/i }));

    expect(screen.getByRole('menuitem', { name: /shell/i })).toBeInTheDocument();
  });

  it('switches the active tab when a tab is clicked', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessions: [
            { terminalId: 'term-1', label: 'Tab 1', cli_type: 'shell', status: 'running' },
            { terminalId: 'term-2', label: 'Tab 2', cli_type: 'shell', status: 'running' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    const secondTab = await screen.findByRole('tab', { name: /tab 2/i });
    fireEvent.click(secondTab);

    expect(secondTab).toHaveAttribute('aria-selected', 'true');
    const panels = screen.getAllByRole('tabpanel', { hidden: true });
    expect(panels[0]).toHaveAttribute('aria-hidden', 'true');
    expect(panels[1]).toHaveAttribute('aria-hidden', 'false');
  });

  it('closes a tab and keeps the remaining tab visible', async () => {
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sessions: [
              { terminalId: 'term-1', label: 'Tab 1', cli_type: 'shell', status: 'running' },
              { terminalId: 'term-2', label: 'Tab 2', cli_type: 'shell', status: 'running' },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await screen.findByRole('tab', { name: /tab 1/i });
    const closeButton = screen.getByRole('button', { name: /close tab 1/i });
    fireEvent.click(closeButton);

    await waitFor(() =>
      expect(screen.queryByRole('tab', { name: /tab 1/i })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole('tab', { name: /tab 2/i })).toBeInTheDocument();
  });

  it('spawns a default shell session when the backend has no existing terminals', async () => {
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ sessions: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ terminalId: 'term-1', label: 'Shell 1' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      );

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(screen.getByRole('tab', { name: /shell 1/i })).toBeInTheDocument());
    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      'http://localhost:8080/terminal/api/terminal/spawn',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ cli_type: 'shell' }),
      }),
    );
  });

  it('shows the unavailable notice when default session spawn fails', async () => {
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ sessions: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 500 }));

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() =>
      expect(
        screen.getByText('This backend does not expose the legacy terminal transport yet.'),
      ).toBeInTheDocument(),
    );
  });

  it('sends resize and terminal input events through the websocket for the active tab', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessions: [
            { terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(screen.getByRole('tab', { name: /shell 1/i })).toBeInTheDocument());
    await waitForTerminalWiring();
    expect(xtermInstances).toHaveLength(1);

    latestWebSocketOptions?.onOpen?.();
    expect(mockSendJson).toHaveBeenCalledWith({ type: 'resize', cols: 80, rows: 24 });

    onDataCallbacks[0]?.('ls -la\r');
    expect(mockSendJson).toHaveBeenCalledWith({ type: 'input', data: 'ls -la\r' });

    onResizeCallbacks[0]?.({ cols: 120, rows: 40 });
    expect(mockSendJson).toHaveBeenCalledWith({ type: 'resize', cols: 120, rows: 40 });

    await waitFor(() => expect(mockXtermFocus).toHaveBeenCalled());
    expect(mockFitAddonFit).toHaveBeenCalled();
    expect(mockXtermRefresh).toHaveBeenCalledWith(0, 23);
  });

  it('writes websocket output, exit markers, and raw payloads to the terminal', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessions: [
            { terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(screen.getByRole('tab', { name: /shell 1/i })).toBeInTheDocument());

    await waitForTerminalWiring();
    latestWebSocketOptions?.onMessage?.(JSON.stringify({ type: 'output', data: 'hello' }));
    latestWebSocketOptions?.onMessage?.(JSON.stringify({ type: 'exit' }));
    latestWebSocketOptions?.onMessage?.('not-json');

    expect(mockXtermWrite).toHaveBeenCalledWith('hello');
    expect(mockXtermWrite).toHaveBeenCalledWith('\r\n\x1b[90m[Process exited]\x1b[0m\r\n');
    expect(mockXtermWrite).toHaveBeenCalledWith('not-json');
  });

  it('updates the connection badge when websocket events close or error', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessions: [
            { terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(screen.getByText('connecting…')).toBeInTheDocument());
    await waitForSocket();
    latestWebSocketOptions?.onOpen?.();
    await waitFor(() => expect(screen.getByText('connected')).toBeInTheDocument());

    latestWebSocketOptions?.onClose?.();
    await waitFor(() => expect(screen.getByText('connecting…')).toBeInTheDocument());

    latestWebSocketOptions?.onOpen?.();
    await waitFor(() => expect(screen.getByText('connected')).toBeInTheDocument());

    latestWebSocketOptions?.onError?.();
    await waitFor(() => expect(screen.getByText('connecting…')).toBeInTheDocument());
  });

  it('ignores websocket events until there is an active terminal instance', async () => {
    let resolveFontsReady: (() => void) | null = null;
    const fonts = {
      ready: new Promise<void>((resolve) => {
        resolveFontsReady = resolve;
      }),
      load: vi.fn().mockResolvedValue(undefined),
    } as unknown as Document['fonts'];
    setDocumentFonts(fonts);
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessions: [
            { terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(screen.getByRole('tab', { name: /shell 1/i })).toBeInTheDocument());
    expect(mockXtermOpen).not.toHaveBeenCalled();

    // Socket only: this test is precisely about there being no terminal yet.
    await waitForSocket();
    latestWebSocketOptions?.onOpen?.();
    latestWebSocketOptions?.onMessage?.('{"type":"output","data":"ignored"}');

    expect(mockSendJson).not.toHaveBeenCalled();
    expect(mockXtermWrite).not.toHaveBeenCalled();

    resolveFontsReady?.();
    await waitFor(() => expect(mockXtermOpen).toHaveBeenCalled());
  });

  it('waits for document fonts and proceeds when font loading rejects', async () => {
    const fonts = {
      ready: Promise.resolve(),
      load: vi.fn().mockRejectedValue(new Error('font load failed')),
    } as unknown as Document['fonts'];
    setDocumentFonts(fonts);
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessions: [
            { terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(fonts.load).toHaveBeenCalled());
    await waitFor(() => expect(mockXtermOpen).toHaveBeenCalled());
  });

  it('avoids setting font-ready state after unmount while fonts are still loading', async () => {
    let resolveFontsReady: (() => void) | null = null;
    const fonts = {
      ready: new Promise<void>((resolve) => {
        resolveFontsReady = resolve;
      }),
      load: vi.fn().mockResolvedValue(undefined),
    } as unknown as Document['fonts'];
    setDocumentFonts(fonts);
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessions: [
            { terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    const view = render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(screen.getByRole('tab', { name: /shell 1/i })).toBeInTheDocument());
    view.unmount();

    resolveFontsReady?.();
    await Promise.resolve();

    expect(mockXtermOpen).not.toHaveBeenCalled();
  });

  it('does not mount xterm input handlers in read-only mode', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessions: [
            { terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" readOnly />);

    await waitFor(() => expect(screen.getByRole('tab', { name: /shell 1/i })).toBeInTheDocument());
    expect(mockXtermOnData).not.toHaveBeenCalled();
    expect(mockXtermOnResize).toHaveBeenCalled();
  });

  it('reacts to resize-observer callbacks after mounting the terminal', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessions: [
            { terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(mockXtermOpen).toHaveBeenCalled());
    await waitFor(() => expect(resizeObservers).toHaveLength(1));

    const fitCallsBeforeResize = mockFitAddonFit.mock.calls.length;
    resizeObservers[0]?.callback([] as ResizeObserverEntry[], resizeObservers[0] as ResizeObserver);

    expect(mockFitAddonFit.mock.calls.length).toBeGreaterThan(fitCallsBeforeResize);
  });

  it('closes the new-terminal menu when clicking outside', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessions: [
            { terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(screen.getByRole('tab', { name: /shell 1/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /new terminal/i }));
    expect(screen.getByRole('menu')).toBeInTheDocument();

    fireEvent.mouseDown(document.body);

    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
  });

  it('keeps the new-terminal menu open when clicking inside it', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessions: [
            { terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(screen.getByRole('tab', { name: /shell 1/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /new terminal/i }));

    const shellMenuItem = screen.getByRole('menuitem', { name: /shell/i });
    fireEvent.mouseDown(shellMenuItem);

    expect(screen.getByRole('menu')).toBeInTheDocument();
  });

  it('keeps the menu open when spawning a new terminal fails', async () => {
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sessions: [
              { terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 500 }));

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(screen.getByRole('tab', { name: /shell 1/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /new terminal/i }));
    fireEvent.click(screen.getByRole('menuitem', { name: /bash/i }));

    expect(screen.getByRole('menu')).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /bash/i })).not.toBeInTheDocument();
  });

  it('keeps the only tab open when its close button is unavailable', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessions: [
            { terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(screen.getByRole('tab', { name: /shell 1/i })).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /close shell 1/i })).not.toBeInTheDocument();
  });

  it('keeps the current tab active when closing a different tab', async () => {
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sessions: [
              { terminalId: 'term-1', label: 'Tab 1', cli_type: 'shell', status: 'running' },
              { terminalId: 'term-2', label: 'Tab 2', cli_type: 'shell', status: 'running' },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    const secondTab = await screen.findByRole('tab', { name: /tab 2/i });
    fireEvent.click(secondTab);
    fireEvent.click(screen.getByRole('button', { name: /close tab 1/i }));

    await waitFor(() =>
      expect(screen.queryByRole('tab', { name: /tab 1/i })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole('tab', { name: /tab 2/i })).toHaveAttribute('aria-selected', 'true');
  });

  it('closes a tab cleanly even when no terminal instance has mounted yet', async () => {
    let resolveFontsReady: (() => void) | null = null;
    const fonts = {
      ready: new Promise<void>((resolve) => {
        resolveFontsReady = resolve;
      }),
      load: vi.fn().mockResolvedValue(undefined),
    } as unknown as Document['fonts'];
    setDocumentFonts(fonts);
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sessions: [
              { terminalId: 'term-1', label: 'Tab 1', cli_type: 'shell', status: 'running' },
              { terminalId: 'term-2', label: 'Tab 2', cli_type: 'shell', status: 'running' },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(screen.getByRole('tab', { name: /tab 1/i })).toBeInTheDocument());
    expect(mockXtermOpen).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /close tab 1/i }));

    await waitFor(() =>
      expect(screen.queryByRole('tab', { name: /tab 1/i })).not.toBeInTheDocument(),
    );

    resolveFontsReady?.();
  });

  it('skips terminal refresh when the xterm instance has no refresh method', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessions: [
            { terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    render(<SessionTerminalLive url="ws://localhost:8080/terminal/ws" />);

    await waitFor(() => expect(screen.getByRole('tab', { name: /shell 1/i })).toBeInTheDocument());
    xtermInstances[0].refresh = undefined;

    await waitFor(() => expect(mockXtermFocus).toHaveBeenCalled());
    expect(mockXtermRefresh).not.toHaveBeenCalled();
  });
});
