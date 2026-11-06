import { syncTerminalTheme } from './Terminal/syncTerminalTheme';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { getAuthHeaders } from '@niuulabs/query';
import { ErrorState, LoadingState } from '@niuulabs/ui';
import { Plus, RotateCw, X } from 'lucide-react';
import { Terminal as XTerm } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';
import styles from './SessionTerminalLive.module.css';
import { useWebSocket } from './hooks/useWebSocket';

const FONT_LOAD_TIMEOUT_MS = 2_000;
const TERMINAL_FONT = '13px "JetBrainsMono NF"';
const NERD_FONT_FAMILY =
  '"JetBrainsMono NF", var(--font-mono), "JetBrains Mono", "Fira Code", monospace';

interface SessionTerminalLiveProps {
  url: string | null;
  readOnly?: boolean;
  /** Closes the panel the terminal sits in; its shells keep running on the host. */
  onClose?: () => void;
  /** What to say while there is no terminal to reach, such as a session still starting. */
  unavailableMessage?: string;
}

interface TerminalTab {
  id: string;
  label: string;
  cliType: string;
  /** An agent's terminal shows its work without taking keys until the person allows it. */
  viewOnly: boolean;
}

interface ServerSession {
  terminalId: string;
  label: string;
  cli_type: string;
  status: string;
}

interface TerminalInstance {
  term: XTerm;
  fitAddon: FitAddon;
  stopThemeSync: () => void;
}

/** The shells the devrunner terminal server starts (`containers/devrunner/terminal.py`). */
const SHELL_CLI_TYPES: ReadonlySet<string> = new Set(['shell', 'bash', 'zsh', 'fish']);

/** An agent CLI (claude, codex, aider) rather than a person's shell. */
export function isAgentTerminal(cliType: string): boolean {
  return !SHELL_CLI_TYPES.has(cliType);
}

export function deriveHttpBase(wsUrl: string): string {
  const httpProto = wsUrl.startsWith('wss:') ? 'https:' : 'http:';
  const parsed = new URL(wsUrl);
  const prefix = parsed.pathname.replace(/\/ws\/?$/, '');
  return `${httpProto}//${parsed.host}${prefix}`;
}

export async function listSessions(httpBase: string): Promise<ServerSession[] | null> {
  const headers = Object.fromEntries(getAuthHeaders().entries());

  const resp = await fetch(`${httpBase}/api/terminal/sessions`, { headers });
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`Could not load terminals (HTTP ${resp.status}).`);
  if (resp.headers.get('content-type')?.includes('text/html')) return null;
  let data: { sessions?: ServerSession[] };
  try {
    data = await resp.json();
  } catch {
    throw new Error('The terminal endpoint returned an invalid response.');
  }
  if (!Array.isArray(data.sessions))
    throw new Error('The terminal endpoint did not return a session list.');
  return data.sessions;
}

export async function spawnSession(
  httpBase: string,
  cliType: string,
): Promise<{ terminalId: string; label: string } | null> {
  const headers = Object.fromEntries(
    getAuthHeaders({ 'Content-Type': 'application/json' }).entries(),
  );

  const resp = await fetch(`${httpBase}/api/terminal/spawn`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ cli_type: cliType }),
  });
  if (!resp.ok) return null;
  const data = (await resp.json()) as { terminalId: string; label?: string };
  return { terminalId: data.terminalId, label: data.label || data.terminalId };
}

export async function killSession(httpBase: string, terminalId: string): Promise<void> {
  const headers = Object.fromEntries(
    getAuthHeaders({ 'Content-Type': 'application/json' }).entries(),
  );

  try {
    await fetch(`${httpBase}/api/terminal/kill`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ terminalId }),
    });
  } catch {
    // Best-effort only.
  }
}

export function SessionTerminalLive(props: SessionTerminalLiveProps) {
  // Reconnecting starts the connection afresh: the host keeps its terminals, so they come back.
  const [connection, setConnection] = useState(0);
  return (
    <SessionTerminalConnection
      key={`${props.url}:${connection}`}
      {...props}
      onReconnect={() => setConnection((value) => value + 1)}
    />
  );
}

function SessionTerminalConnection({
  url,
  readOnly = false,
  onClose,
  unavailableMessage = 'terminal unavailable',
  onReconnect,
}: SessionTerminalLiveProps & { onReconnect: () => void }) {
  const [tabs, setTabs] = useState<TerminalTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [fontReady, setFontReady] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [terminalError, setTerminalError] = useState<string | null>(null);

  const containerRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const instanceRefs = useRef<Map<string, TerminalInstance>>(new Map());
  const initialisedRef = useRef(false);

  const httpBase = useMemo(() => (url ? deriveHttpBase(url) : null), [url]);

  const activeWsUrl = useMemo(() => {
    if (!url || !activeTabId || !fontReady) {
      return null;
    }
    const base = url.replace(/\/ws\/?$/, '');
    return `${base}/ws/${activeTabId}`;
  }, [url, activeTabId, fontReady]);

  const writeToTab = useCallback((tabId: string, data: string) => {
    instanceRefs.current.get(tabId)?.term.write(data);
  }, []);

  const socketTabId = activeTabId;

  const { sendJson } = useWebSocket(activeWsUrl, {
    snapshotHandlersPerConnection: true,
    onOpen: () => {
      setConnected(true);
      setTerminalError(null);
      if (!socketTabId) return;
      const instance = instanceRefs.current.get(socketTabId);
      if (!instance) return;
      sendJson({ type: 'resize', cols: instance.term.cols, rows: instance.term.rows });
    },
    onMessage: (raw: string) => {
      if (!socketTabId) return;
      try {
        const msg = JSON.parse(raw) as { type: string; data?: string };
        if (msg.type === 'output' && msg.data) {
          writeToTab(socketTabId, msg.data);
          return;
        }
        if (msg.type === 'exit') {
          writeToTab(socketTabId, '\r\n\x1b[90m[Process exited]\x1b[0m\r\n');
          return;
        }
      } catch {
        // Fall through and write raw payload.
      }

      writeToTab(socketTabId, raw);
    },
    onClose: () => setConnected(false),
    onError: () => {
      setConnected(false);
      setTerminalError(
        'The terminal connection failed. Check that the host supports terminal WebSockets.',
      );
    },
  });

  useEffect(() => {
    let cancelled = false;

    async function waitForFont() {
      const fonts = document.fonts;
      if (!fonts) {
        setFontReady(true);
        return;
      }

      await fonts.ready;

      try {
        await Promise.race([
          fonts.load(TERMINAL_FONT),
          new Promise((resolve) => setTimeout(resolve, FONT_LOAD_TIMEOUT_MS)),
        ]);
      } catch {
        // Proceed with fallback fonts.
      }

      if (!cancelled) {
        setFontReady(true);
      }
    }

    void waitForFont();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!httpBase || initialisedRef.current) return;
    initialisedRef.current = true;
    let cancelled = false;
    void (async () => {
      try {
        const existing = await listSessions(httpBase);
        if (cancelled) return;
        if (existing === null) {
          setUnavailable(true);
          return;
        }
        if (existing.length > 0) {
          const restored = existing.map((session, index) => ({
            id: session.terminalId,
            label: session.label || `Terminal ${index + 1}`,
            cliType: session.cli_type,
            viewOnly: isAgentTerminal(session.cli_type),
          }));
          setTabs(restored);
          setActiveTabId(restored[0]?.id ?? null);
          return;
        }
        if (readOnly) {
          setTerminalError('No terminal session is available to view.');
          return;
        }
        const created = await spawnSession(httpBase, 'shell');
        if (cancelled) return;
        if (!created) throw new Error('The host could not start a terminal.');
        setTabs([
          {
            id: created.terminalId,
            label: created.label || 'Terminal 1',
            cliType: 'shell',
            viewOnly: false,
          },
        ]);
        setActiveTabId(created.terminalId);
      } catch (error: unknown) {
        if (!cancelled)
          setTerminalError(
            error instanceof Error ? error.message : 'Could not reach the terminal service.',
          );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      initialisedRef.current = false;
    };
  }, [httpBase, readOnly]);

  const mountTerminal = useCallback(
    (tab: TerminalTab) => {
      const tabId = tab.id;
      const container = containerRefs.current.get(tabId);
      if (!container || !fontReady) {
        return;
      }

      if (instanceRefs.current.has(tabId)) {
        return;
      }

      const term = new XTerm({
        cursorBlink: true,
        cursorStyle: 'block',
        fontFamily: NERD_FONT_FAMILY,
        fontSize: 13,
        lineHeight: 1.4,
        disableStdin: readOnly || tab.viewOnly,
        allowProposedApi: true,
        scrollback: 5_000,
        theme: {
          background: '#09090b',
          foreground: '#a1a1aa',
          cursor: '#f97316',
          cursorAccent: '#09090b',
          selectionBackground: '#f9731640',
          selectionForeground: '#fafafa',
          black: '#09090b',
          red: '#ef4444',
          green: '#10b981',
          yellow: '#f59e0b',
          blue: '#3b82f6',
          magenta: '#a855f7',
          cyan: '#06b6d4',
          white: '#a1a1aa',
          brightBlack: '#52525b',
          brightRed: '#f87171',
          brightGreen: '#34d399',
          brightYellow: '#fbbf24',
          brightBlue: '#60a5fa',
          brightMagenta: '#c084fc',
          brightCyan: '#22d3ee',
          brightWhite: '#fafafa',
        },
      });

      const fitAddon = new FitAddon();
      const webLinksAddon = new WebLinksAddon();

      term.loadAddon(fitAddon);
      term.loadAddon(webLinksAddon);
      term.open(container);
      const stopThemeSync = syncTerminalTheme(term, container);

      try {
        fitAddon.fit();
      } catch {
        // Container might not be visible yet.
      }

      instanceRefs.current.set(tabId, { term, fitAddon, stopThemeSync });
    },
    [fontReady, readOnly],
  );

  useEffect(() => {
    if (!fontReady) {
      return;
    }

    for (const tab of tabs) {
      mountTerminal(tab);
    }
  }, [fontReady, tabs, mountTerminal]);

  useEffect(() => {
    const instances = instanceRefs.current;
    const containers = containerRefs.current;

    return () => {
      for (const instance of instances.values()) {
        instance.stopThemeSync();
        instance.term.dispose();
      }
      instances.clear();
      containers.clear();
    };
  }, []);

  const activeTab = tabs.find((tab) => tab.id === activeTabId) ?? null;
  const activeViewOnly = activeTab?.viewOnly ?? false;

  useEffect(() => {
    if (!activeTabId) return;
    const instance = instanceRefs.current.get(activeTabId);
    if (!instance || readOnly || activeViewOnly) return;

    const disposable = instance.term.onData((data: string) => {
      sendJson({ type: 'input', data });
    });

    return () => disposable.dispose();
  }, [activeTabId, activeViewOnly, readOnly, sendJson, fontReady]);

  useEffect(() => {
    if (!activeTabId) return;
    const instance = instanceRefs.current.get(activeTabId);
    if (!instance) return;

    const disposable = instance.term.onResize(({ cols, rows }) => {
      sendJson({ type: 'resize', cols, rows });
    });

    return () => disposable.dispose();
  }, [activeTabId, sendJson, fontReady]);

  useEffect(() => {
    if (!activeTabId) return;
    const container = containerRefs.current.get(activeTabId);
    if (!container) return;

    const observer = new ResizeObserver(() => {
      try {
        instanceRefs.current.get(activeTabId)?.fitAddon.fit();
      } catch {
        // Ignore fit errors during transitions.
      }
    });

    observer.observe(container);
    return () => observer.disconnect();
  }, [activeTabId]);

  useEffect(() => {
    if (!activeTabId) return;

    const timer = setTimeout(() => {
      const instance = instanceRefs.current.get(activeTabId);
      if (!instance) return;

      try {
        instance.fitAddon.fit();
        instance.term.focus();
        if ('refresh' in instance.term && typeof instance.term.refresh === 'function') {
          instance.term.refresh(0, Math.max(instance.term.rows - 1, 0));
        }
      } catch {
        // Ignore fit errors during display transitions.
      }
    }, 50);

    return () => clearTimeout(timer);
  }, [activeTabId]);

  const handleAddShell = useCallback(async () => {
    if (!httpBase || readOnly) return;
    let created: Awaited<ReturnType<typeof spawnSession>>;
    try {
      created = await spawnSession(httpBase, 'shell');
    } catch {
      setTerminalError('Could not reach the terminal service.');
      return;
    }
    if (!created) {
      setTerminalError('The host could not start a terminal.');
      return;
    }
    setTerminalError(null);
    setTabs((prev) => [
      ...prev,
      {
        id: created.terminalId,
        label: created.label || `Terminal ${prev.length + 1}`,
        cliType: 'shell',
        viewOnly: false,
      },
    ]);
    setActiveTabId(created.terminalId);
  }, [httpBase, readOnly]);

  // ⌃⇧` opens another shell, as in VS Code: the browser keeps ⌘T for its own tabs.
  useEffect(() => {
    if (readOnly || !httpBase) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (!event.ctrlKey || !event.shiftKey || event.code !== 'Backquote') return;
      event.preventDefault();
      void handleAddShell();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [handleAddShell, httpBase, readOnly]);

  const enableInput = useCallback((tabId: string) => {
    const instance = instanceRefs.current.get(tabId);
    if (instance) instance.term.options.disableStdin = false;
    setTabs((prev) => prev.map((tab) => (tab.id === tabId ? { ...tab, viewOnly: false } : tab)));
    instance?.term.focus();
  }, []);

  const handleCloseTab = useCallback(
    async (tabId: string) => {
      if (tabs.length <= 1) return;
      if (httpBase) {
        await killSession(httpBase, tabId);
      }

      setTabs((prev) => {
        const closedIndex = prev.findIndex((tab) => tab.id === tabId);
        const next = prev.filter((tab) => tab.id !== tabId);
        if (tabId === activeTabId) {
          const nextActive = next[Math.min(closedIndex, next.length - 1)] ?? null;
          setActiveTabId(nextActive?.id ?? null);
        }

        const instance = instanceRefs.current.get(tabId);
        if (instance) {
          instance.stopThemeSync();
          instance.term.dispose();
          instanceRefs.current.delete(tabId);
        }
        containerRefs.current.delete(tabId);

        return next;
      });
    },
    [activeTabId, httpBase, tabs.length],
  );

  const handleSelectTab = useCallback((tabId: string) => {
    setActiveTabId(tabId);
  }, []);

  const header = (
    <div className={styles.header}>
      <div className={styles.tabs} role="tablist" aria-label="Terminals">
        {tabs.map((tab) => (
          <div key={tab.id} className={styles.tabWrap}>
            <button
              type="button"
              role="tab"
              aria-selected={activeTabId === tab.id}
              onClick={() => handleSelectTab(tab.id)}
              className={styles.tab}
            >
              <span>{tab.label}</span>
              {tab.viewOnly && !readOnly ? (
                <span className={styles.viewOnly}>view only</span>
              ) : null}
            </button>
            {tabs.length > 1 && !readOnly && (
              <button
                type="button"
                aria-label={`Close ${tab.label}`}
                className={styles.tabClose}
                onClick={() => void handleCloseTab(tab.id)}
              >
                <X aria-hidden="true" />
              </button>
            )}
          </div>
        ))}
        {!readOnly && httpBase && !loading && !unavailable ? (
          <button
            type="button"
            aria-label="New shell"
            title="New shell (⌃⇧`)"
            className={styles.tool}
            onClick={() => void handleAddShell()}
          >
            <Plus aria-hidden="true" />
          </button>
        ) : null}
      </div>
      {activeTab?.viewOnly && !readOnly ? (
        <button
          type="button"
          className={styles.textTool}
          onClick={() => enableInput(activeTab.id)}
          title="This is the agent's terminal; typing here goes to the agent's process"
        >
          Enable input
        </button>
      ) : null}
      {url && !loading && !unavailable && tabs.length > 0 ? (
        <span className={styles.status} data-testid="terminal-connection">
          {connected ? 'connected' : 'connecting…'}
        </span>
      ) : null}
      {url ? (
        <button
          type="button"
          className={styles.textTool}
          onClick={onReconnect}
          title="Reconnect to this session's terminals"
        >
          <RotateCw aria-hidden="true" />
          Reconnect
        </button>
      ) : null}
      {onClose ? (
        <button
          type="button"
          className={styles.tool}
          aria-label="Close the terminal"
          title="Close the terminal (⌃`)"
          onClick={onClose}
        >
          <X aria-hidden="true" />
        </button>
      ) : null}
    </div>
  );

  let body: ReactNode;
  if (!url) {
    body = <p className={styles.notice}>{unavailableMessage}</p>;
  } else if (loading) {
    body = <LoadingState className="forge-session-loading" label="Connecting to terminal…" />;
  } else if (unavailable) {
    // The host answered, but serves no terminal for this session: say so, never fake one.
    body = (
      <div className={styles.notice} data-testid="terminal-not-served">
        <div>
          <b>No terminal yet</b>
          {"This session's host serves no terminal. Container sessions get one from their pod; a "}
          {'session on a VM needs a terminal server in the VM and a '}
          <code>/terminal</code>
          {" route through Forge's session proxy, which do not exist yet."}
        </div>
      </div>
    );
  } else if (terminalError && tabs.length === 0) {
    body = (
      <ErrorState
        className="forge-session-loading"
        title="Could not connect to terminal"
        message={terminalError}
        action={
          <button type="button" className="niuu-chat-retry" onClick={onReconnect}>
            Try again
          </button>
        }
      />
    );
  }

  return (
    <div className={styles.root} data-testid="session-terminal">
      {header}
      {body ?? (
        <>
          {terminalError && (
            <p role="alert" className="niuu:p-3 niuu:text-sm niuu:text-critical-fg">
              {terminalError}
            </p>
          )}
          <div className={styles.terminalArea}>
            {tabs.map((tab) => (
              <div
                key={tab.id}
                role="tabpanel"
                aria-hidden={tab.id !== activeTabId}
                data-terminal-id={tab.id}
                data-visible={tab.id === activeTabId}
                className={styles.terminalContainer}
                style={{ display: tab.id === activeTabId ? 'block' : 'none' }}
                ref={(element) => {
                  if (element) {
                    containerRefs.current.set(tab.id, element);
                    mountTerminal(tab);
                  } else {
                    containerRefs.current.delete(tab.id);
                  }
                }}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
