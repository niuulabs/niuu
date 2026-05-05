import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, waitFor, fireEvent } from '@testing-library/react';
import { Terminal } from './Terminal';
import type { IPtyStream } from '../../ports/IPtyStream';

// ---------------------------------------------------------------------------
// Mock xterm — Canvas is not available in jsdom.
// ---------------------------------------------------------------------------

const mockXtermWrite = vi.fn();
const mockXtermOnData = vi.fn();
const mockXtermOpen = vi.fn();
const mockXtermDispose = vi.fn();
const mockXtermLoadAddon = vi.fn();
const mockFitAddonFit = vi.fn();
const mockFitAddonDispose = vi.fn();

let capturedOnData: ((data: string) => void) | null = null;

vi.mock('@xterm/xterm', () => ({
  Terminal: vi.fn().mockImplementation(() => ({
    open: mockXtermOpen,
    write: mockXtermWrite,
    dispose: mockXtermDispose,
    loadAddon: mockXtermLoadAddon,
    onData: mockXtermOnData.mockImplementation((cb: (data: string) => void) => {
      capturedOnData = cb;
      return { dispose: vi.fn() };
    }),
    options: {},
  })),
}));

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: vi.fn().mockImplementation(() => ({
    fit: mockFitAddonFit,
    dispose: mockFitAddonDispose,
  })),
}));

// ---------------------------------------------------------------------------
// ResizeObserver stub
// ---------------------------------------------------------------------------

class ResizeObserverStub {
  observe = vi.fn();
  disconnect = vi.fn();
  unobserve = vi.fn();
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildStream(overrides?: Partial<IPtyStream>): IPtyStream {
  const subscribers: Array<(chunk: string) => void> = [];
  return {
    subscribe: vi.fn((_id, cb) => {
      subscribers.push(cb);
      return () => {
        const idx = subscribers.indexOf(cb);
        if (idx !== -1) subscribers.splice(idx, 1);
      };
    }),
    send: vi.fn(),
    ...overrides,
  };
}

function emit(stream: IPtyStream, chunk: string) {
  // Access the internal call to trigger subscriber callbacks.
  const calls = (stream.subscribe as ReturnType<typeof vi.fn>).mock.calls;
  const lastCall = calls.at(-1);
  if (lastCall) {
    const cb = lastCall[1] as (chunk: string) => void;
    cb(chunk);
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  capturedOnData = null;
});

afterEach(() => {
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Terminal', () => {
  it('opens xterm on the container div', async () => {
    const stream = buildStream();
    render(<Terminal sessionId="sess-1" stream={stream} />);
    await waitFor(() => expect(mockXtermOpen).toHaveBeenCalledWith(expect.any(HTMLDivElement)));
  });

  it('subscribes to the PTY stream on mount', async () => {
    const stream = buildStream();
    render(<Terminal sessionId="sess-1" stream={stream} />);
    await waitFor(() =>
      expect(stream.subscribe).toHaveBeenCalledWith('sess-1', expect.any(Function)),
    );
  });

  it('writes incoming PTY data to xterm', async () => {
    const stream = buildStream();
    render(<Terminal sessionId="sess-1" stream={stream} />);
    await waitFor(() => expect(stream.subscribe).toHaveBeenCalled());

    act(() => {
      emit(stream, '$ hello\r\n');
    });

    expect(mockXtermWrite).toHaveBeenCalledWith('$ hello\r\n');
  });

  it('sends keyboard input to the stream when not read-only', async () => {
    const stream = buildStream();
    render(<Terminal sessionId="sess-1" stream={stream} />);
    await waitFor(() => expect(mockXtermOnData).toHaveBeenCalled());

    act(() => {
      capturedOnData?.('ls\r');
    });

    expect(stream.send).toHaveBeenCalledWith('sess-1', 'ls\r');
  });

  it('does NOT wire onData when readOnly=true (input disabled)', async () => {
    const stream = buildStream();
    render(<Terminal sessionId="sess-1" stream={stream} readOnly />);
    await waitFor(() => expect(stream.subscribe).toHaveBeenCalled());
    // onData should not be registered in read-only mode.
    expect(mockXtermOnData).not.toHaveBeenCalled();
  });

  it('shows the read-only badge when readOnly=true', async () => {
    const stream = buildStream();
    render(<Terminal sessionId="sess-1" stream={stream} readOnly />);
    await waitFor(() =>
      expect(screen.queryByTestId('terminal-readonly-badge')).toBeInTheDocument(),
    );
  });

  it('does NOT show the read-only badge in interactive mode', () => {
    const stream = buildStream();
    render(<Terminal sessionId="sess-1" stream={stream} />);
    expect(screen.queryByTestId('terminal-readonly-badge')).not.toBeInTheDocument();
  });

  it('re-subscribes when sessionId changes (reconnect)', async () => {
    const stream = buildStream();
    const { rerender } = render(<Terminal sessionId="sess-1" stream={stream} />);
    await waitFor(() =>
      expect(stream.subscribe).toHaveBeenCalledWith('sess-1', expect.any(Function)),
    );

    rerender(<Terminal sessionId="sess-2" stream={stream} />);
    await waitFor(() =>
      expect(stream.subscribe).toHaveBeenCalledWith('sess-2', expect.any(Function)),
    );
  });

  it('re-subscribes when stream changes (reconnect)', async () => {
    const stream1 = buildStream();
    const stream2 = buildStream();
    const { rerender } = render(<Terminal sessionId="sess-1" stream={stream1} />);
    await waitFor(() => expect(stream1.subscribe).toHaveBeenCalled());

    rerender(<Terminal sessionId="sess-1" stream={stream2} />);
    await waitFor(() =>
      expect(stream2.subscribe).toHaveBeenCalledWith('sess-1', expect.any(Function)),
    );
  });

  it('unsubscribes on unmount', async () => {
    const unsubscribe = vi.fn();
    const stream = buildStream({ subscribe: vi.fn().mockReturnValue(unsubscribe) });
    const { unmount } = render(<Terminal sessionId="sess-1" stream={stream} />);
    await waitFor(() => expect(stream.subscribe).toHaveBeenCalled());

    unmount();
    expect(unsubscribe).toHaveBeenCalled();
  });

  it('shows a "connecting" status badge before the first data chunk', async () => {
    // Stream that never sends data.
    const stream = buildStream({ subscribe: vi.fn().mockReturnValue(() => {}) });
    render(<Terminal sessionId="sess-1" stream={stream} />);
    await waitFor(() =>
      expect(screen.getByTestId('terminal-connection-status')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('terminal-connection-status')).toHaveTextContent(/connecting/i);
  });

  it('hides the status badge once data arrives', async () => {
    const stream = buildStream();
    render(<Terminal sessionId="sess-1" stream={stream} />);
    await waitFor(() => expect(stream.subscribe).toHaveBeenCalled());

    act(() => emit(stream, '$ '));

    await waitFor(() =>
      expect(screen.queryByTestId('terminal-connection-status')).not.toBeInTheDocument(),
    );
  });

  it('shows a reconnect button in interactive mode', async () => {
    const stream = buildStream();
    render(<Terminal sessionId="sess-1" stream={stream} />);
    expect(screen.getByTestId('terminal-reconnect-button')).toBeInTheDocument();
  });

  it('triggers reconnect on reconnect button click', async () => {
    const stream = buildStream();
    // Pass reconnectDelayMs=0 so the reconnect fires synchronously.
    render(<Terminal sessionId="sess-1" stream={stream} reconnectDelayMs={0} />);
    await waitFor(() => expect(stream.subscribe).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByTestId('terminal-reconnect-button'));

    await waitFor(() => expect(stream.subscribe).toHaveBeenCalledTimes(2));
  });

  it('passes paste data through to the stream', async () => {
    const stream = buildStream();
    render(<Terminal sessionId="sess-1" stream={stream} />);
    await waitFor(() => expect(mockXtermOnData).toHaveBeenCalled());

    const pasteContent = 'echo hello world\r';
    act(() => {
      capturedOnData?.(pasteContent);
    });

    expect(stream.send).toHaveBeenCalledWith('sess-1', pasteContent);
  });
});
