import { useEffect, useRef, useCallback, useState } from 'react';
import { cn } from '@niuulabs/ui';
import type { IPtyStream } from '../../ports/IPtyStream';
import '@xterm/xterm/css/xterm.css';

const DEFAULT_RECONNECT_DELAY_MS = 3_000;
const FONT_LOAD_TIMEOUT_MS = 2_000;
const TERMINAL_FONT = '10px "JetBrainsMono Nerd Font"';
const TERMINAL_FONT_FAMILY =
  '"JetBrainsMono Nerd Font", "JetBrains Mono", "Cascadia Code", "Fira Code", monospace';

export interface TerminalProps {
  sessionId: string;
  stream: IPtyStream;
  /** When true, keyboard input is disabled (archived/read-only sessions). */
  readOnly?: boolean;
  /**
   * Milliseconds to wait before attempting a reconnect.
   * Override in tests to avoid real delays.
   * @default 3000
   */
  reconnectDelayMs?: number;
  className?: string;
}

interface TerminalHandle {
  xterm: unknown;
  dispose: () => void;
}

/** Mounts an xterm.js terminal connected to the given IPtyStream. */
export function Terminal({
  sessionId,
  stream,
  readOnly = false,
  reconnectDelayMs = DEFAULT_RECONNECT_DELAY_MS,
  className,
}: TerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const handleRef = useRef<TerminalHandle | null>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [connectionState, setConnectionState] = useState<
    'connecting' | 'connected' | 'reconnecting'
  >('connecting');
  const [fontReady, setFontReady] = useState(false);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const connect = useCallback(() => {
    if (!handleRef.current) return;
    const { xterm } = handleRef.current as {
      xterm: {
        write: (s: string) => void;
        onData: (cb: (d: string) => void) => { dispose: () => void };
      };
    };

    if (unsubscribeRef.current) {
      unsubscribeRef.current();
      unsubscribeRef.current = null;
    }

    setConnectionState('connecting');

    const unsub = stream.subscribe(sessionId, (chunk) => {
      xterm.write(chunk);
      setConnectionState('connected');
      clearReconnectTimer();
    });

    unsubscribeRef.current = unsub;

    // Wire up keyboard input (disabled in read-only mode).
    if (!readOnly) {
      const dataDispose = xterm.onData((data: string) => {
        stream.send(sessionId, data);
      });
      const originalUnsub = unsub;
      unsubscribeRef.current = () => {
        dataDispose.dispose();
        originalUnsub();
      };
    }
  }, [sessionId, stream, readOnly, clearReconnectTimer]);

  /** Public reconnect — also triggered automatically on session change. */
  const reconnect = useCallback(() => {
    clearReconnectTimer();
    setConnectionState('reconnecting');
    reconnectTimerRef.current = setTimeout(() => {
      connect();
    }, reconnectDelayMs);
  }, [connect, clearReconnectTimer, reconnectDelayMs]);

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      try {
        await document.fonts.ready;
        await Promise.race([
          document.fonts.load(TERMINAL_FONT),
          new Promise((resolve) => setTimeout(resolve, FONT_LOAD_TIMEOUT_MS)),
        ]);
      } catch {
        // Fall through to mount with fallback fonts.
      }

      if (!cancelled) {
        setFontReady(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // Initialise xterm once and keep it alive for the lifetime of the component.
  useEffect(() => {
    if (!containerRef.current || !fontReady) return;

    let cancelled = false;

    // Dynamic import keeps xterm out of the SSR bundle.
    void (async () => {
      const [{ Terminal: XTerm }, { FitAddon }, { WebLinksAddon }] = await Promise.all([
        import('@xterm/xterm'),
        import('@xterm/addon-fit'),
        import('@xterm/addon-web-links'),
      ]);

      if (cancelled || !containerRef.current) return;

      const fitAddon = new FitAddon();
      const webLinksAddon = new WebLinksAddon();
      const xterm = new XTerm({
        theme: {
          background: '#09090b',
          foreground: '#fafafa',
          cursor: '#a1a1aa',
          selectionBackground: '#3f3f46',
        },
        fontFamily: TERMINAL_FONT_FAMILY,
        fontSize: 10,
        lineHeight: 1.15,
        cursorBlink: !readOnly,
        disableStdin: readOnly,
        scrollback: 5_000,
      });

      xterm.loadAddon(fitAddon);
      xterm.loadAddon(webLinksAddon);
      xterm.open(containerRef.current);
      fitAddon.fit();

      handleRef.current = {
        xterm,
        dispose: () => {
          fitAddon.dispose();
          webLinksAddon.dispose();
          xterm.dispose();
        },
      };

      // Fit on resize.
      const resizeObserver = new ResizeObserver(() => {
        fitAddon.fit();
      });
      resizeObserver.observe(containerRef.current);

      connect();

      handleRef.current.dispose = () => {
        resizeObserver.disconnect();
        fitAddon.dispose();
        webLinksAddon.dispose();
        xterm.dispose();
      };
    })();

    return () => {
      cancelled = true;
      clearReconnectTimer();
      if (unsubscribeRef.current) {
        unsubscribeRef.current();
        unsubscribeRef.current = null;
      }
      if (handleRef.current) {
        handleRef.current.dispose();
        handleRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fontReady]);

  // Re-subscribe when sessionId or stream changes.
  useEffect(() => {
    if (!handleRef.current) return;
    connect();
    return () => {
      clearReconnectTimer();
      if (unsubscribeRef.current) {
        unsubscribeRef.current();
        unsubscribeRef.current = null;
      }
    };
  }, [sessionId, stream, connect, clearReconnectTimer]);

  return (
    <div
      className={cn(
        'niuu-relative niuu-h-full niuu-w-full niuu-overflow-hidden niuu-bg-bg-primary',
        className,
      )}
    >
      {connectionState !== 'connected' && (
        <div
          className="niuu-absolute niuu-right-3 niuu-top-3 niuu-z-10 niuu-flex niuu-items-center niuu-gap-1.5 niuu-rounded-md niuu-bg-bg-elevated niuu-px-2 niuu-py-1 niuu-text-xs niuu-text-text-muted"
          role="status"
          data-testid="terminal-connection-status"
        >
          <span
            className={cn(
              'niuu-inline-block niuu-h-1.5 niuu-w-1.5 niuu-rounded-full niuu-animate-pulse',
              connectionState === 'reconnecting' ? 'niuu-bg-brand' : 'niuu-bg-text-muted',
            )}
          />
          {connectionState === 'reconnecting' ? 'reconnecting…' : 'connecting…'}
        </div>
      )}
      {readOnly && (
        <div
          className="niuu-absolute niuu-left-3 niuu-top-3 niuu-z-10 niuu-rounded-md niuu-bg-bg-elevated niuu-px-2 niuu-py-1 niuu-text-xs niuu-text-text-muted"
          aria-label="read-only terminal"
          data-testid="terminal-readonly-badge"
        >
          read-only
        </div>
      )}
      <div
        ref={containerRef}
        className="niuu-volundr-terminal-root niuu-h-full niuu-w-full"
        data-testid="terminal-container"
        aria-label={readOnly ? 'read-only terminal output' : 'interactive terminal'}
        role="region"
      />
      {!readOnly && (
        <button
          className="niuu-absolute niuu-bottom-3 niuu-right-3 niuu-z-10 niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-px-2.5 niuu-py-1 niuu-text-xs niuu-text-text-muted niuu-opacity-70 hover:niuu-opacity-100"
          onClick={reconnect}
          aria-label="reconnect terminal"
          data-testid="terminal-reconnect-button"
        >
          reconnect
        </button>
      )}
    </div>
  );
}
