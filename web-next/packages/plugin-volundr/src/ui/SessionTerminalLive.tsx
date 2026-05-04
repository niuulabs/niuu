import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getAccessToken } from '@niuulabs/query';
import { cn } from '@niuulabs/ui';
import { Terminal as XTerm } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';
import { useWebSocket } from './hooks/useWebSocket';

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
  const [unavailable, setUnavailable] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  const containerRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const instanceRefs = useRef<Map<string, TerminalInstance>>(new Map());
  const initialisedRef = useRef(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const httpBase = useMemo(() => (url ? deriveHttpBase(url) : null), [url]);
  const activeWsUrl = useMemo(() => {
    if (!url || !activeTabId) return null;
    return `${url.replace(/\/ws\/?$/, '')}/ws/${activeTabId}`;
  }, [activeTabId, url]);

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
      sendJson({
        type: 'resize',
        cols: instance.term.cols,
        rows: instance.term.rows,
      });
    },
    onMessage: (raw: string) => {
      try {
        const msg = JSON.parse(raw) as { type: string; data?: string };
        if (msg.type === 'output' && msg.data) {
          writeToTerminal(msg.data);
        }
        if (msg.type === 'exit') {
          writeToTerminal('\r\n[Process exited]\r\n');
        }
      } catch {
        writeToTerminal(raw);
      }
    },
    onClose: () => setConnected(false),
    onError: () => setConnected(false),
  });

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
      if (!container || instanceRefs.current.has(tabId)) return;
      containerRefs.current.set(tabId, container);

      const term = new XTerm({
        cursorBlink: true,
        cursorStyle: 'block',
        fontFamily: '"JetBrainsMono Nerd Font", "JetBrains Mono", monospace',
        fontSize: 13,
        lineHeight: 1.3,
        disableStdin: readOnly,
        theme: {
          background: '#09090b',
          foreground: '#fafafa',
          cursor: '#a1a1aa',
          selectionBackground: '#3f3f46',
        },
      });
      const fitAddon = new FitAddon();
      const webLinksAddon = new WebLinksAddon();
      term.loadAddon(fitAddon);
      term.loadAddon(webLinksAddon);
      term.open(container);
      fitAddon.fit();

      instanceRefs.current.set(tabId, { term, fitAddon });
    },
    [readOnly],
  );

  useEffect(() => {
    return () => {
      for (const inst of instanceRefs.current.values()) {
        inst.term.dispose();
      }
      instanceRefs.current.clear();
      containerRefs.current.clear();
    };
  }, []);

  useEffect(() => {
    if (!activeTabId || readOnly) return;
    const instance = instanceRefs.current.get(activeTabId);
    if (!instance) return;

    const disposable = instance.term.onData((data) => {
      sendJson({ type: 'input', data });
    });
    return () => disposable.dispose();
  }, [activeTabId, readOnly, sendJson]);

  useEffect(() => {
    if (!activeTabId) return;
    const instance = instanceRefs.current.get(activeTabId);
    if (!instance) return;

    const disposable = instance.term.onResize(({ cols, rows }) => {
      sendJson({ type: 'resize', cols, rows });
    });
    return () => disposable.dispose();
  }, [activeTabId, sendJson]);

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
      try {
        instanceRefs.current.get(activeTabId)?.fitAddon.fit();
      } catch {
        // Ignore fit errors during transitions.
      }
    }, 50);
    return () => clearTimeout(timer);
  }, [activeTabId]);

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

  const handleAddCliTab = useCallback(
    async (cliType: string) => {
      if (!httpBase) return;
      const created = await spawnSession(httpBase, cliType);
      if (!created) return;

      const cliLabel =
        CLI_OPTIONS.find((option) => option.id === cliType)?.label ??
        created.label ??
        `Terminal ${tabs.length + 1}`;

      setTabs((prev) => [
        ...prev,
        {
          id: created.terminalId,
          label: created.label || cliLabel,
          cliType,
          restricted: false,
        },
      ]);
      setActiveTabId(created.terminalId);
      setConnected(false);
      setMenuOpen(false);
    },
    [httpBase, tabs.length],
  );

  const handleCloseTab = useCallback(
    async (tabId: string) => {
      if (tabs.length <= 1) return;
      if (httpBase) {
        await killSession(httpBase, tabId);
      }

      const instance = instanceRefs.current.get(tabId);
      if (instance) {
        instance.term.dispose();
        instanceRefs.current.delete(tabId);
      }
      containerRefs.current.delete(tabId);

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
    },
    [activeTabId, httpBase, tabs.length],
  );

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
          <div
            key={tab.id}
            ref={(node) => mountTerminal(tab.id, node)}
            className={cn(
              'niuu-h-full niuu-w-full',
              activeTabId === tab.id ? 'niuu-block' : 'niuu-hidden',
            )}
          />
        ))}
      </div>
    </div>
  );
}
