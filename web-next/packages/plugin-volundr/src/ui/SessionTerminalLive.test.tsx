import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import {
  deriveHttpBase,
  killSession,
  listSessions,
  SessionTerminalLive,
  spawnSession,
} from './SessionTerminalLive';

vi.mock('@niuulabs/query', () => ({
  getAccessToken: vi.fn(() => 'token-123'),
}));

vi.mock('./hooks/useWebSocket', () => ({
  useWebSocket: vi.fn(() => ({ sendJson: vi.fn() })),
}));

const mockXtermOpen = vi.fn();
const mockXtermWrite = vi.fn();
const mockXtermDispose = vi.fn();
const mockXtermLoadAddon = vi.fn();
const mockXtermOnData = vi.fn(() => ({ dispose: vi.fn() }));
const mockXtermOnResize = vi.fn(() => ({ dispose: vi.fn() }));
const mockFitAddonFit = vi.fn();

vi.mock('@xterm/xterm', () => ({
  Terminal: vi.fn().mockImplementation(() => ({
    open: mockXtermOpen,
    write: mockXtermWrite,
    dispose: mockXtermDispose,
    loadAddon: mockXtermLoadAddon,
    onData: mockXtermOnData,
    onResize: mockXtermOnResize,
    cols: 80,
    rows: 24,
  })),
}));

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: vi.fn().mockImplementation(() => ({
    fit: mockFitAddonFit,
  })),
}));

vi.mock('@xterm/addon-web-links', () => ({
  WebLinksAddon: vi.fn().mockImplementation(() => ({})),
}));

class ResizeObserverStub {
  observe = vi.fn();
  disconnect = vi.fn();
  unobserve = vi.fn();
}

vi.stubGlobal('ResizeObserver', ResizeObserverStub);

describe('SessionTerminalLive helpers', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    global.fetch = vi.fn();
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
      headers: { Authorization: 'Bearer token-123' },
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
        'Content-Type': 'application/json',
        Authorization: 'Bearer token-123',
      },
      body: JSON.stringify({ cli_type: 'shell' }),
    });
  });

  it('kills a terminal session with auth headers', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 204 }));

    await killSession('https://example.com', 'term-9');

    expect(global.fetch).toHaveBeenCalledWith('https://example.com/api/terminal/kill', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer token-123',
      },
      body: JSON.stringify({ terminalId: 'term-9' }),
    });
  });
});

describe('SessionTerminalLive', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 404 }));
    vi.clearAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
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
  });

  it('adds a new CLI tab from the menu', async () => {
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            sessions: [{ terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' }],
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
          sessions: [{ terminalId: 'term-1', label: 'Shell 1', cli_type: 'shell', status: 'running' }],
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
});
