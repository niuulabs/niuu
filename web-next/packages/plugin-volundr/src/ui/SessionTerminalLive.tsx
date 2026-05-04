import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getAccessToken } from '@niuulabs/query';
import { Terminal as XTerm } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';
import styles from './SessionTerminalLive.module.css';
import { SessionTerminalTabBar } from './SessionTerminalTabBar';
import { useWebSocket } from './hooks/useWebSocket';

const FONT_LOAD_TIMEOUT_MS = 2_000;
const TERMINAL_FONT = '13px "JetBrainsMono NF"';
const NERD_FONT_FAMILY =
  '"JetBrainsMono NF", var(--font-mono), "JetBrains Mono", "Fira Code", monospace';

interface SessionTerminalLiveProps {
  url: string | null;
  readOnly?: boolean;
}

interface TerminalTab {
  id: string;
  label: string;
  cliType: string;
  restricted?: boolean;
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
}

const CLI_OPTIONS = [
  { id: 'shell', label: 'Shell' },
  { id: 'bash', label: 'Bash' },
  { id: 'zsh', label: 'Zsh' },
  { id: 'fish', label: 'Fish' },
  { id: 'claude', label: 'Claude' },
  { id: 'codex', label: 'Codex' },
  { id: 'aider', label: 'Aider' },
] as const;

export function deriveHttpBase(wsUrl: string): string {
  const httpProto = wsUrl.startsWith('wss:') ? 'https:' : 'http:';
  const parsed = new URL(wsUrl);
  const prefix = parsed.pathname.replace(/\/ws\/?$/, '');
  return `${httpProto}//${parsed.host}${prefix}`;
}

export async function listSessions(httpBase: string): Promise<ServerSession[] | null> {
  const headers: Record<string, string> = {};
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetch(`${httpBase}/api/terminal/sessions`, { headers });
  if (resp.status === 404) return null;
  if (!resp.ok) return [];
  const data = (await resp.json()) as { sessions?: ServerSession[] };
  return data.sessions ?? [];
}

export async function spawnSession(
  httpBase: string,
  cliType: string,
): Promise<{ terminalId: string; label: string } | null> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;

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
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;

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

export function SessionTerminalLive({ url, readOnly = false }: SessionTerminalLiveProps) {
  const [tabs, setTabs] = useState<TerminalTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [fontReady, setFontReady] = useState(false);
  const [unavailable, setUnavailable] = useState(false);

  const containerRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const instanceRefs = useRef<Map<string, TerminalInstance>>(new Map());
  const initialisedRef = useRef(false);

  const httpBase = useMemo(() => (url ? deriveHttpBase(url) : null), [url]);

  const activeWsUrl = useMemo(() => {
    if (!url || !activeTabId) {
      return null;
    }
    const base = url.replace(/\/ws\/?$/, '');
    return `${base}/ws/${activeTabId}`;
  }, [url, activeTabId]);

  const writeToTerminal = useCallback(
    (data: string) => {
      if (!activeTabId) return;
      instanceRefs.current.get(activeTabId)?.term.write(data);
    },
    [activeTabId],
  );

  const { sendJson } = useWebSocket(activeWsUrl, {
    onOpen: () => {
      setConnected(true);
      if (!activeTabId) return;
      const instance = instanceRefs.current.get(activeTabId);
      if (!instance) return;
      sendJson({ type: 'resize', cols: instance.term.cols, rows: instance.term.rows });
    },
    onMessage: (raw: string) => {
      try {
        const msg = JSON.parse(raw) as { type: string; data?: string };
        if (msg.type === 'output' && msg.data) {
          writeToTerminal(msg.data);
          return;
        }
        if (msg.type === 'exit') {
          writeToTerminal('\r\n\x1b[90m[Process exited]\x1b[0m\r\n');
          return;
        }
      } catch {
        // Fall through and write raw payload.
      }

      writeToTerminal(raw);
    },
    onClose: () => setConnected(false),
    onError: () => setConnected(false),
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

    (async () => {
      const existing = await listSessions(httpBase);
      if (existing === null) {
        setUnavailable(true);
        return;
      }
      if (existing.length > 0) {
        const restored = existing.map((session, index) => ({
          id: session.terminalId,
          label: session.label || `Terminal ${index + 1}`,
          cliType: session.cli_type,
          restricted: false,
        }));
        setTabs(restored);
        setActiveTabId(restored[0]?.id ?? null);
        return;
      }

      const created = await spawnSession(httpBase, 'shell');
      if (!created) {
        setUnavailable(true);
        return;
      }
      setTabs([
        { id: created.terminalId, label: created.label || 'Terminal 1', cliType: 'shell' },
      ]);
      setActiveTabId(created.terminalId);
    })();
  }, [httpBase]);

  const mountTerminal = useCallback(
    (tabId: string, container: HTMLDivElement | null) => {
      if (!container || !fontReady) {
        return;
      }

      if (instanceRefs.current.has(tabId)) {
        return;
      }

      containerRefs.current.set(tabId, container);

      const term = new XTerm({
        cursorBlink: true,
        cursorStyle: 'block',
        fontFamily: NERD_FONT_FAMILY,
        fontSize: 13,
        lineHeight: 1.4,
        disableStdin: readOnly,
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

      try {
        fitAddon.fit();
      } catch {
        // Container might not be visible yet.
      }

      instanceRefs.current.set(tabId, { term, fitAddon });
    },
    [fontReady, readOnly],
  );

  useEffect(() => {
    const instances = instanceRefs.current;
    const containers = containerRefs.current;

    return () => {
      for (const instance of instances.values()) {
        instance.term.dispose();
      }
      instances.clear();
      containers.clear();
    };
  }, []);

  useEffect(() => {
    if (!activeTabId) return;
    const instance = instanceRefs.current.get(activeTabId);
    if (!instance || readOnly) return;

    const disposable = instance.term.onData((data: string) => {
      sendJson({ type: 'input', data });
    });

    return () => disposable.dispose();
  }, [activeTabId, readOnly, sendJson, fontReady]);

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

  const handleAddCliTab = useCallback(
    async (cliType: string) => {
      if (!httpBase) return;

      const created = await spawnSession(httpBase, cliType);
      if (!created) return;

      setTabs((prev) => {
        const cliLabel =
          CLI_OPTIONS.find((option) => option.id === cliType)?.label ??
          created.label ??
          `Terminal ${prev.length + 1}`;

        return [
          ...prev,
          {
            id: created.terminalId,
            label: created.label || cliLabel,
            cliType,
            restricted: false,
          },
        ];
      });
      setActiveTabId(created.terminalId);
    },
    [httpBase],
  );

  const handleAddTab = useCallback(() => {
    void handleAddCliTab('shell');
  }, [handleAddCliTab]);

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

  if (!url) {
    return (
      <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-text-sm niuu-text-text-muted">
        terminal unavailable
      </div>
    );
  }

  if (unavailable) {
    return (
      <div className="niuu-flex niuu-h-full niuu-items-center niuu-justify-center niuu-p-6 niuu-text-center niuu-text-sm niuu-text-text-muted">
        This backend does not expose the legacy terminal transport yet.
      </div>
    );
  }

  return (
    <div className={styles.wrapper}>
      <div className={styles.toolbar}>
        <div className={styles.statusIndicator} data-connected={connected}>
          <span>{connected ? 'Connected' : 'Disconnected'}</span>
        </div>
      </div>

      <SessionTerminalTabBar
        tabs={tabs}
        activeTabId={activeTabId ?? ''}
        onSelectTab={handleSelectTab}
        onCloseTab={(tabId) => void handleCloseTab(tabId)}
        onAddTab={handleAddTab}
        onAddCliTab={(cliType) => void handleAddCliTab(cliType)}
      />

      <div className={styles.terminalArea}>
        {tabs.map((tab) => (
          <div
            key={tab.id}
            role="tabpanel"
            aria-hidden={tab.id !== activeTabId}
            data-terminal-id={tab.id}
            data-visible={tab.id === activeTabId}
            className={styles.terminalContainer}
            ref={(element) => {
              if (element) {
                mountTerminal(tab.id, element);
              }
            }}
          />
        ))}
      </div>
    </div>
  );
}
