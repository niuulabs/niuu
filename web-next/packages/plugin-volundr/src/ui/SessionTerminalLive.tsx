import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getAccessToken } from '@niuulabs/query';
import { cn } from '@niuulabs/ui';
import { Terminal as XTerm } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';
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

interface LiveTerminalPaneProps {
  active: boolean;
  onConnectionChange: (connected: boolean) => void;
  readOnly: boolean;
  tabId: string;
  wsBaseUrl: string;
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

function buildTerminalWsUrl(wsBaseUrl: string, tabId: string): string {
  return `${wsBaseUrl.replace(/\/ws\/?$/, '')}/ws/${tabId}`;
}

function LiveTerminalPane({
  active,
  onConnectionChange,
  readOnly,
  tabId,
  wsBaseUrl,
}: LiveTerminalPaneProps) {
  const [fontReady, setFontReady] = useState(false);
  const [isConnected, setIsConnected] = useState(false);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const instanceRef = useRef<TerminalInstance | null>(null);

  const wsUrl = useMemo(() => buildTerminalWsUrl(wsBaseUrl, tabId), [tabId, wsBaseUrl]);

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
    if (!active) return;
    onConnectionChange(isConnected);
  }, [active, isConnected, onConnectionChange]);

  useEffect(() => {
    if (!containerRef.current || !fontReady || instanceRef.current) return;

    const fitAddon = new FitAddon();
    const webLinksAddon = new WebLinksAddon();
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

    term.loadAddon(fitAddon);
    term.loadAddon(webLinksAddon);
    term.open(containerRef.current);

    try {
      fitAddon.fit();
    } catch {
      // Container may not be visible yet.
    }

    instanceRef.current = { term, fitAddon };

    const observer = new ResizeObserver(() => {
      try {
        instanceRef.current?.fitAddon.fit();
      } catch {
        // Ignore fit errors during layout transitions.
      }
    });
    observer.observe(containerRef.current);

    return () => {
      observer.disconnect();
      instanceRef.current?.term.dispose();
      instanceRef.current = null;
    };
  }, [fontReady, readOnly]);

  const { sendJson } = useWebSocket(wsUrl, {
    onOpen: () => {
      setIsConnected(true);
      const instance = instanceRef.current;
      if (!instance) return;
      sendJson({
        type: 'resize',
        cols: instance.term.cols,
        rows: instance.term.rows,
      });
    },
    onMessage: (raw: string) => {
      const instance = instanceRef.current;
      if (!instance) return;

      try {
        const msg = JSON.parse(raw) as { type: string; data?: string };
        if (msg.type === 'output' && msg.data) {
          instance.term.write(msg.data);
          return;
        }
        if (msg.type === 'exit') {
          instance.term.write('\r\n\x1b[90m[Process exited]\x1b[0m\r\n');
          return;
        }
      } catch {
        // Fall through and write raw payload.
      }

      instance.term.write(raw);
    },
    onClose: () => setIsConnected(false),
    onError: () => setIsConnected(false),
  });

  useEffect(() => {
    if (readOnly) return;
    const instance = instanceRef.current;
    if (!instance) return;

    const disposable = instance.term.onData((data) => {
      sendJson({ type: 'input', data });
    });

    return () => disposable.dispose();
  }, [fontReady, readOnly, sendJson]);

  useEffect(() => {
    const instance = instanceRef.current;
    if (!instance) return;

    const disposable = instance.term.onResize(({ cols, rows }) => {
      sendJson({ type: 'resize', cols, rows });
    });

    return () => disposable.dispose();
  }, [fontReady, sendJson]);

  useEffect(() => {
    if (!active) return;

    const timer = setTimeout(() => {
      const instance = instanceRef.current;
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
  }, [active, fontReady]);

  return (
    <div
      ref={containerRef}
      role="tabpanel"
      aria-hidden={!active}
      data-visible={active}
      className={cn('niuu-h-full niuu-w-full niuu-p-2', active ? 'niuu-block' : 'niuu-hidden')}
    />
  );
}

export function SessionTerminalLive({ url, readOnly = false }: SessionTerminalLiveProps) {
  const [tabs, setTabs] = useState<TerminalTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  const initialisedRef = useRef(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const httpBase = useMemo(() => (url ? deriveHttpBase(url) : null), [url]);

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

  useEffect(() => {
    if (!menuOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [menuOpen]);

  const handleAddCliTab = useCallback(async (cliType: string) => {
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
    setConnected(false);
    setMenuOpen(false);
  }, [httpBase]);

  const handleCloseTab = useCallback(async (tabId: string) => {
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
        setConnected(false);
      }
      return next;
    });
  }, [activeTabId, httpBase, tabs.length]);

  const handleSelectTab = useCallback((tabId: string) => {
    setActiveTabId(tabId);
    setConnected(false);
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
    <div className="niuu-flex niuu-h-full niuu-min-h-0 niuu-flex-col niuu-bg-bg-primary">
      <div className="niuu-flex niuu-items-center niuu-justify-between niuu-border-b niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-3 niuu-py-2">
        <div className="niuu-flex niuu-items-center niuu-gap-1.5" role="tablist">
          {tabs.map((tab) => (
            <div key={tab.id} className="niuu-flex niuu-items-center niuu-gap-1">
              <button
                type="button"
                role="tab"
                aria-selected={activeTabId === tab.id}
                onClick={() => handleSelectTab(tab.id)}
                className={cn(
                  'niuu-flex niuu-items-center niuu-gap-2 niuu-rounded-md niuu-border niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-[11px]',
                  activeTabId === tab.id
                    ? 'niuu-border-border niuu-bg-bg-elevated niuu-text-text-primary'
                    : 'niuu-border-transparent niuu-text-text-muted hover:niuu-border-border-subtle hover:niuu-text-text-secondary',
                )}
              >
                <span className="niuu-text-brand">{'>_'}</span>
                <span>{tab.label}</span>
              </button>
              {tabs.length > 1 && (
                <button
                  type="button"
                  aria-label={`Close ${tab.label}`}
                  className="niuu-rounded niuu-px-1.5 niuu-py-1 niuu-font-mono niuu-text-[11px] niuu-text-text-muted hover:niuu-bg-bg-elevated hover:niuu-text-text-primary"
                  onClick={() => void handleCloseTab(tab.id)}
                >
                  x
                </button>
              )}
            </div>
          ))}
          <div className="niuu-relative" ref={menuRef}>
            <button
              type="button"
              aria-label="New terminal"
              aria-expanded={menuOpen}
              aria-haspopup="menu"
              className="niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-elevated niuu-px-2.5 niuu-py-1.5 niuu-font-mono niuu-text-[11px] niuu-text-text-muted hover:niuu-text-text-primary"
              onClick={() => setMenuOpen((prev) => !prev)}
            >
              +
            </button>
            {menuOpen && (
              <div
                role="menu"
                className="niuu-absolute niuu-left-0 niuu-top-[calc(100%+6px)] niuu-z-20 niuu-min-w-32 niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-elevated niuu-p-1 niuu-shadow-lg"
              >
                {CLI_OPTIONS.map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    role="menuitem"
                    className="niuu-flex niuu-w-full niuu-items-center niuu-justify-start niuu-rounded-sm niuu-px-2.5 niuu-py-1.5 niuu-text-left niuu-text-xs niuu-text-text-secondary hover:niuu-bg-bg-secondary hover:niuu-text-text-primary"
                    onClick={() => void handleAddCliTab(option.id)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
          {connected ? 'connected' : 'connecting…'}
        </div>
      </div>
      <div className="niuu-min-h-0 niuu-flex-1 niuu-overflow-hidden">
        {tabs.map((tab) => (
          <LiveTerminalPane
            key={tab.id}
            active={activeTabId === tab.id}
            onConnectionChange={setConnected}
            readOnly={readOnly}
            tabId={tab.id}
            wsBaseUrl={url}
          />
        ))}
      </div>
    </div>
  );
}
