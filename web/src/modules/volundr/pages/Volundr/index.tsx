import { useState, useEffect, useMemo, useCallback, useRef, lazy, Suspense } from 'react';
import {
  Hammer,
  Activity,
  Database,
  BarChart3,
  Zap,
  Server,
  DollarSign,
  Plus,
  Play,
  Square,
  Trash2,
  FolderGit2,
  MessageSquare,
  Terminal,
  Code,
  FileText,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Maximize2,
  ScrollText,
  Archive,
  RotateCcw,
  GitCompareArrows,
  Menu,
  Pencil,
  Check,
  ExternalLink,
  FolderOpen,
  List,
  LayoutGrid,
} from 'lucide-react';
import { MetricCard, Modal, SearchInput, StatusBadge, StatusDot } from '@/modules/shared';
import { SessionCard } from '@/modules/volundr/components/organisms/SessionCard';
import { SessionTerminal } from '@/modules/volundr/components/SessionTerminal';
import { SessionChat } from '@/modules/shared/components/SessionChat';
import { SessionChronicles } from '@/modules/volundr/components/SessionChronicles';
import { SessionStartingIndicator } from '@/modules/volundr/components/molecules/SessionStartingIndicator';
import { SessionDiffs } from '@/modules/volundr/components/SessionDiffs';
import { SessionGroupList } from '@/modules/volundr/components/SessionGroupList';
import { FileManager } from '@/modules/volundr/components/FileManager';
import { DeleteSessionDialog } from '@/modules/volundr/components/DeleteSessionDialog';
import type { CleanupTarget } from '@/modules/volundr/components/DeleteSessionDialog';
import { LaunchWizard } from '@/modules/volundr/components/LaunchWizard';
import type { LaunchConfig } from '@/modules/volundr/components/LaunchWizard';
import { useVolundr } from '@/modules/volundr/hooks/useVolundr';
import { useLocalStorage } from '@/hooks/useLocalStorage';
import { useSessionProbe } from '@/modules/volundr/hooks/useSessionStartingPoll';
import { useDiffViewer } from '@/modules/volundr/hooks/useDiffViewer';
import { volundrService } from '@/modules/volundr/adapters';
import { getAccessToken } from '@/modules/volundr/adapters/api/client';
import type {
  VolundrSession,
  VolundrLog,
  FeatureModule,
  UserFeaturePreference,
} from '@/modules/volundr/models';
import { isSessionBooting, isSessionActive } from '@/modules/volundr/models';
import { resolveIcon } from '@/modules/icons';
import { formatTokens, cn } from '@/utils';
import { getRepo, getBranch, getSourceLabel, isGitSource } from '@/utils/source';
import styles from './VolundrPage.module.css';

const EditorPanel = lazy(() =>
  import('@/modules/volundr/components/EditorPanel/EditorPanel').then(m => ({
    default: m.EditorPanel,
  }))
);

const STATUS_OPTIONS = ['all', 'running', 'stopped', 'error'];

type TabId = 'chat' | 'terminal' | 'code' | 'files' | 'diffs' | 'chronicles' | 'logs';

export function VolundrPage() {
  const {
    stats,
    sessions,
    models,
    repos,
    templates,
    loading,
    updateSession,
    stopSession,
    resumeSession,
    startSession,
    deleteSession,
    archiveSession,
    restoreSession: restoreArchivedSession,
    archivedSessions,
    archiveAllStopped,
    markSessionRunning,
    logs,
    logLoading,
    getLogs,
    chronicle,
    chronicleLoading,
    getChronicle,
    pullRequest,
    prLoading,
    prCreating,
    prMerging,
    fetchPullRequest,
    createPullRequest,
    mergePullRequest,
    refreshCIStatus,
    availableMcpServers,
    availableSecrets,
    presets,
    saveTemplate,
    savePreset,
    searchTrackerIssues,
    updateTrackerIssueStatus,
  } = useVolundr();
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedSession, setSelectedSession] = useState<VolundrSession | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>('chat');
  const [pendingDiffFile, setPendingDiffFile] = useState<string | null>(null);
  const [showLaunchWizard, setShowLaunchWizard] = useState(false);
  const defaultPanels: FeatureModule[] = [
    {
      key: 'chat',
      label: 'Chat',
      icon: 'MessageSquare',
      scope: 'session',
      enabled: true,
      defaultEnabled: true,
      adminOnly: false,
      order: 10,
    },
    {
      key: 'terminal',
      label: 'Terminal',
      icon: 'Terminal',
      scope: 'session',
      enabled: true,
      defaultEnabled: true,
      adminOnly: false,
      order: 20,
    },
    {
      key: 'code',
      label: 'Code',
      icon: 'Code',
      scope: 'session',
      enabled: true,
      defaultEnabled: true,
      adminOnly: false,
      order: 30,
    },
    {
      key: 'files',
      label: 'Files',
      icon: 'FolderOpen',
      scope: 'session',
      enabled: true,
      defaultEnabled: true,
      adminOnly: false,
      order: 40,
    },
    {
      key: 'diffs',
      label: 'Diffs',
      icon: 'GitCompareArrows',
      scope: 'session',
      enabled: true,
      defaultEnabled: true,
      adminOnly: false,
      order: 50,
    },
    {
      key: 'chronicles',
      label: 'Chronicles',
      icon: 'ScrollText',
      scope: 'session',
      enabled: true,
      defaultEnabled: true,
      adminOnly: false,
      order: 60,
    },
    {
      key: 'logs',
      label: 'Logs',
      icon: 'FileText',
      scope: 'session',
      enabled: true,
      defaultEnabled: true,
      adminOnly: false,
      order: 70,
    },
  ];
  const [sessionPanels, setSessionPanels] = useState<FeatureModule[]>(defaultPanels);
  const [panelPrefs, setPanelPrefs] = useState<UserFeaturePreference[]>([]);

  useEffect(() => {
    Promise.all([
      volundrService.getFeatureModules('session'),
      volundrService.getUserFeaturePreferences(),
    ]).then(([panels, prefs]) => {
      if (panels.length > 0) {
        setSessionPanels(panels);
      }
      setPanelPrefs(prefs);
    });
  }, []);
  const [sidebarCollapsed, setSidebarCollapsed] = useLocalStorage(
    'volundr-sidebar-collapsed',
    false
  );
  const [compactCards, setCompactCards] = useLocalStorage('volundr-compact-cards', false);
  const [statsCollapsed, setStatsCollapsed] = useLocalStorage('volundr-stats-collapsed', true);
  const [archivedCollapsed, setArchivedCollapsed] = useLocalStorage(
    'volundr-archived-collapsed',
    true
  );

  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);

  const [isLaunching, setIsLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);

  // Session name editing state
  const [editingName, setEditingName] = useState(false);
  const [editNameValue, setEditNameValue] = useState('');

  // Delete session dialog state
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);

  // Live message count from the WebSocket chat (syncs sidebar badge)
  const [liveChatCount, setLiveChatCount] = useState<number | null>(null);

  // Session-host logs (fetched directly from session's /api/logs)
  const [sessionHostLogs, setSessionHostLogs] = useState<VolundrLog[]>([]);
  const [sessionHostLogLoading, setSessionHostLogLoading] = useState(false);
  const logPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Resolve the selected session from the live sessions array so SSE updates
  // (e.g. status changing from 'starting' to 'running') are reflected immediately.
  const effectiveSelectedSession = useMemo(() => {
    if (selectedSession) {
      return sessions.find(s => s.id === selectedSession.id) ?? selectedSession;
    }
    return sessions.length > 0 ? sessions[0] : null;
  }, [selectedSession, sessions]);
  // Resolve the repo URL for PR lookups (session stores org/name, API needs full URL)
  const sessionRepoUrl = useMemo(() => {
    const repo = effectiveSelectedSession ? getRepo(effectiveSelectedSession.source) : '';
    if (!repo) {
      return '';
    }
    const match = repos.find(r => `${r.org}/${r.name}` === repo || r.url === repo);
    return match?.url ?? '';
  }, [effectiveSelectedSession, repos]);

  // Track whether the selected session's WebSocket has been verified as
  // connectable.  This prevents showing the chat/terminal before the
  // container is actually ready to accept connections.
  const [connectionVerified, setConnectionVerified] = useState(false);

  // Reset verification when a different session is selected.
  const [prevSelectedId, setPrevSelectedId] = useState(effectiveSelectedSession?.id);
  if (prevSelectedId !== effectiveSelectedSession?.id) {
    setPrevSelectedId(effectiveSelectedSession?.id);
    setConnectionVerified(false);
    setLiveChatCount(null);
  }

  // Build WebSocket URLs from the session's chat endpoint.
  // Gateway-routed sessions include a path prefix: /s/{session-id}/api/session
  const sessionHost = effectiveSelectedSession?.hostname ?? null;
  const chatEndpoint = effectiveSelectedSession?.chatEndpoint ?? null;
  const isRunning = effectiveSelectedSession?.status === 'running';

  const {
    files: liveFiles,
    filesLoading: liveFilesLoading,
    diff,
    diffLoading,
    diffError,
    selectedFile,
    diffBase,
    fetchFiles,
    selectFile,
    setDiffBase,
  } = useDiffViewer(chatEndpoint);

  const probeWsUrl = useMemo(() => {
    if (chatEndpoint) return chatEndpoint;
    if (!sessionHost) return null;
    const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${wsProto}//${sessionHost}/session`;
  }, [chatEndpoint, sessionHost]);

  const handleSessionReady = useCallback(() => {
    setConnectionVerified(true);
    if (
      effectiveSelectedSession?.status === 'starting' ||
      effectiveSelectedSession?.status === 'provisioning'
    ) {
      markSessionRunning(effectiveSelectedSession.id);
    }
  }, [effectiveSelectedSession, markSessionRunning]);

  const needsProbe = !!sessionHost && !connectionVerified;
  useSessionProbe({
    url: probeWsUrl,
    enabled: needsProbe,
    onReady: handleSessionReady,
  });

  const isSessionReady = isRunning && (connectionVerified || !sessionHost);

  const terminalWsUrl = useMemo(() => {
    if (!sessionHost || !isSessionReady) {
      return null;
    }
    const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Gateway-routed: derive terminal path from chat endpoint base
    // Chat endpoint: ws(s)://host/s/{id}/session → terminal: ws(s)://host/s/{id}/terminal/ws
    if (chatEndpoint) {
      try {
        const parsed = new URL(chatEndpoint);
        const prefix = parsed.pathname.replace(/\/(api\/)?session$/, '');
        return `${wsProto}//${parsed.host}${prefix}/terminal/ws`;
      } catch {
        /* fall through */
      }
    }
    return `${wsProto}//${sessionHost}/terminal/ws`;
  }, [sessionHost, chatEndpoint, isSessionReady]);

  const chatWsUrl = useMemo(() => {
    // Dev override: ?ravn_ws=ws://localhost:7477/ws
    const params = new URLSearchParams(window.location.search);
    const ravnWs = params.get('ravn_ws');
    if (ravnWs) return ravnWs;

    if (!sessionHost || !isSessionReady) {
      return null;
    }
    if (chatEndpoint) return chatEndpoint;
    const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${wsProto}//${sessionHost}/session`;
  }, [sessionHost, chatEndpoint, isSessionReady]);

  // Fetch logs from session host's /api/logs or fall back to Volundr API
  const fetchSessionHostLogs = useCallback(
    async (hostname: string, sessionId: string, endpoint?: string) => {
      try {
        // Derive logs URL from chat endpoint (preserves /s/{id} prefix)
        // Chat endpoint: wss://host/s/{id}/session → logs: https://host/s/{id}/api/logs
        // Also handles legacy format: wss://host/s/{id}/api/session → https://host/s/{id}/api/logs
        let logsUrl: string;
        if (endpoint) {
          try {
            const parsed = new URL(endpoint);
            const protocol = parsed.protocol === 'wss:' ? 'https:' : 'http:';
            // Strip /session or /api/session to get the session base path
            const basePath = parsed.pathname.replace(/\/(api\/)?session$/, '');
            logsUrl = `${protocol}//${parsed.host}${basePath}/api/logs`;
          } catch {
            logsUrl = `https://${hostname}/api/logs`;
          }
        } else {
          logsUrl = `https://${hostname}/api/logs`;
        }
        const headers: Record<string, string> = {};
        const token = getAccessToken();
        if (token) {
          headers['Authorization'] = `Bearer ${token}`;
        }
        const response = await fetch(logsUrl, { headers });
        if (!response.ok) {
          return;
        }
        const data: unknown = await response.json();

        // The API returns { session_id, total, returned, lines: [...] }
        // or it may return a bare array for backwards compatibility.
        let entries: unknown[];
        if (Array.isArray(data)) {
          entries = data;
        } else if (
          data !== null &&
          typeof data === 'object' &&
          'lines' in data &&
          Array.isArray((data as Record<string, unknown>).lines)
        ) {
          entries = (data as Record<string, unknown>).lines as unknown[];
        } else {
          return;
        }

        const mapped: VolundrLog[] = entries.map((entry: unknown, i: number) => {
          const e = entry as Record<string, unknown>;
          const rawLevel = typeof e.level === 'string' ? e.level.toLowerCase() : '';
          return {
            id: typeof e.id === 'string' ? e.id : `log-${sessionId}-${i}`,
            sessionId,
            timestamp:
              typeof e.timestamp === 'string'
                ? new Date(e.timestamp).getTime()
                : typeof e.timestamp === 'number'
                  ? // Unix seconds (< 1e12) → convert to ms; already ms otherwise
                    e.timestamp < 1e12
                    ? e.timestamp * 1000
                    : e.timestamp
                  : Date.now(),
            level: ['debug', 'info', 'warn', 'error'].includes(rawLevel)
              ? (rawLevel as VolundrLog['level'])
              : 'info',
            source:
              typeof e.source === 'string'
                ? e.source
                : typeof e.logger === 'string'
                  ? e.logger
                  : 'session',
            message: typeof e.message === 'string' ? e.message : String(entry),
          };
        });
        setSessionHostLogs(mapped);
      } catch {
        // Silently fail — session may not be ready yet
      }
    },
    []
  );

  useEffect(() => {
    if (logPollRef.current) {
      clearInterval(logPollRef.current);
      logPollRef.current = null;
    }

    if (!effectiveSelectedSession?.id) {
      setSessionHostLogs([]);
      return;
    }

    const sessionId = effectiveSelectedSession.id;
    const hostname = effectiveSelectedSession.hostname;
    const sessionChatEndpoint = effectiveSelectedSession.chatEndpoint;
    const isActive = effectiveSelectedSession.status === 'running';

    // Running sessions with a hostname: fetch from session's /api/logs
    if (hostname && isActive) {
      setSessionHostLogLoading(true);
      fetchSessionHostLogs(hostname, sessionId, sessionChatEndpoint).finally(() =>
        setSessionHostLogLoading(false)
      );

      // Poll every 5 seconds for live updates
      logPollRef.current = setInterval(() => {
        fetchSessionHostLogs(hostname, sessionId, sessionChatEndpoint);
      }, 5000);

      return () => {
        if (logPollRef.current) {
          clearInterval(logPollRef.current);
          logPollRef.current = null;
        }
      };
    }

    // Fallback: use Volundr API for non-running sessions
    getLogs(sessionId);

    return undefined;
  }, [
    effectiveSelectedSession?.id,
    effectiveSelectedSession?.hostname,
    effectiveSelectedSession?.chatEndpoint,
    effectiveSelectedSession?.status,
    effectiveSelectedSession?.source,
    getLogs,
    fetchSessionHostLogs,
  ]);

  const filteredSessions = sessions.filter(session => {
    if (statusFilter !== 'all' && session.status !== statusFilter) {
      return false;
    }
    if (searchQuery && !session.name.toLowerCase().includes(searchQuery.toLowerCase())) {
      return false;
    }
    return true;
  });

  const tabs = useMemo(() => {
    const prefMap = new Map(panelPrefs.map(p => [p.featureKey, p]));

    // Filter to enabled + visible panels
    const visible = sessionPanels.filter(f => {
      if (!f.enabled) return false;
      const pref = prefMap.get(f.key);
      if (pref && !pref.visible) return false;
      return true;
    });

    // Sort by user preference, then default order
    visible.sort((a, b) => {
      const prefA = prefMap.get(a.key);
      const prefB = prefMap.get(b.key);
      const orderA = prefA !== undefined ? prefA.sortOrder : a.order;
      const orderB = prefB !== undefined ? prefB.sortOrder : b.order;
      return orderA - orderB;
    });

    // Icon fallback map for panels that need special handling
    const fallbackIcons: Record<string, typeof MessageSquare> = {
      chat: MessageSquare,
      terminal: Terminal,
      code: Code,
      files: FolderOpen,
      diffs: GitCompareArrows,
      chronicles: ScrollText,
      logs: FileText,
    };

    return visible.map(f => ({
      id: f.key as TabId,
      label: f.label,
      icon: resolveIcon(f.icon) ?? fallbackIcons[f.key] ?? MessageSquare,
    }));
  }, [sessionPanels, panelPrefs]);

  const codeTabEnabled = tabs.some(t => t.id === 'code');

  const selectedModel = effectiveSelectedSession ? models[effectiveSelectedSession.model] : null;
  const isLocal = selectedModel?.provider === 'local';

  const handleStopSession = async () => {
    if (!effectiveSelectedSession) return;
    try {
      await stopSession(effectiveSelectedSession.id);
    } catch (err) {
      console.error('Failed to stop session:', err);
    }
  };

  const handleResumeSession = async () => {
    if (!effectiveSelectedSession) return;
    try {
      await resumeSession(effectiveSelectedSession.id);
    } catch (err) {
      console.error('Failed to resume session:', err);
    }
  };

  const handleDeleteSession = () => {
    if (!effectiveSelectedSession) {
      return;
    }
    setShowDeleteDialog(true);
  };

  const handleDeleteConfirm = async (cleanup: CleanupTarget[]) => {
    if (!effectiveSelectedSession) {
      return;
    }
    setShowDeleteDialog(false);
    await deleteSession(effectiveSelectedSession.id, cleanup);
    setSelectedSession(null);
  };

  const handleDeleteCancel = () => {
    setShowDeleteDialog(false);
  };

  const handleArchiveSession = async () => {
    if (!effectiveSelectedSession) {
      return;
    }

    if (isSessionActive(effectiveSelectedSession.status)) {
      const confirmed = window.confirm(
        `"${effectiveSelectedSession.name}" is still running. This will stop the session first and then archive it. Continue?`
      );
      if (!confirmed) {
        return;
      }
    }

    await archiveSession(effectiveSelectedSession.id);
    setSelectedSession(null);
  };

  const handleRestoreSession = async (sessionId: string) => {
    await restoreArchivedSession(sessionId);
  };

  const handleArchiveAllStopped = async () => {
    const stoppedCount = sessions.filter(s => s.status === 'stopped').length;
    if (stoppedCount === 0) {
      return;
    }

    const confirmed = window.confirm(
      `Archive ${stoppedCount} stopped session${stoppedCount > 1 ? 's' : ''}?`
    );
    if (!confirmed) {
      return;
    }

    await archiveAllStopped();
    setSelectedSession(null);
  };

  const stoppedSessionCount = sessions.filter(s => s.status === 'stopped').length;

  const formatArchivedDate = (date?: Date): string => {
    if (!date) {
      return '';
    }
    const d = date instanceof Date ? date : new Date(date);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  };

  const handleLaunchSession = useCallback(
    async (config: LaunchConfig) => {
      setIsLaunching(true);
      setLaunchError(null);
      try {
        const session = await startSession({
          name: config.name,
          source: config.source,
          model: config.model,
          templateName: config.templateName,
          definition: config.definition,
          taskType: config.taskType,
          trackerIssue: config.trackerIssue,
          terminalRestricted: config.terminalRestricted,
          workspaceId: config.workspaceId,
          credentialNames: config.credentialNames,
          integrationIds: config.integrationIds,
          resourceConfig: config.resourceConfig,
          systemPrompt: config.systemPrompt,
          initialPrompt: config.initialPrompt,
        });
        setSelectedSession(session);
        setShowLaunchWizard(false);
      } catch (err) {
        console.error('[Volundr] Failed to launch session:', err);
        setLaunchError(err instanceof Error ? err.message : 'Failed to launch session');
      } finally {
        setIsLaunching(false);
      }
    },
    [startSession]
  );

  const handlePopout = (tabType: 'terminal' | 'code' | 'chat') => {
    if (effectiveSelectedSession) {
      sessionStorage.setItem(
        `volundr-popout-session-${effectiveSelectedSession.id}`,
        JSON.stringify(effectiveSelectedSession)
      );
      const url = `/volundr/popout?session=${effectiveSelectedSession.id}&tab=${tabType}`;
      window.open(
        url,
        `volundr-${effectiveSelectedSession.id}-${tabType}`,
        'width=1200,height=800'
      );
    }
  };

  const handleChatMessageCount = useCallback((count: number) => {
    setLiveChatCount(count);
  }, []);

  const formatLogTimestamp = (timestamp: number): string => {
    const date = new Date(timestamp);
    return date.toLocaleTimeString('en-US', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      fractionalSecondDigits: 3,
    });
  };

  const activeSessions = sessions.filter(s => s.status === 'running');

  if (loading || !stats) {
    return (
      <div className={styles.page}>
        <div className={styles.loading}>Loading...</div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      {/* ═══════════════ SIDEBAR ═══════════════ */}
      {sidebarCollapsed ? (
        <div className={styles.sidebarRail}>
          <button
            type="button"
            className={styles.toggleButton}
            onClick={() => setSidebarCollapsed(false)}
            aria-label="Expand sidebar"
          >
            <ChevronRight className={styles.toggleIcon} />
          </button>
          <button
            type="button"
            className={styles.toggleButton}
            onClick={() => setShowLaunchWizard(true)}
            aria-label="New Session"
            title="New Session"
          >
            <Plus className={styles.toggleIcon} />
          </button>
          <span className={styles.sessionCountBadge}>{activeSessions.length}</span>
          <div className={styles.sessionStatusDots}>
            {filteredSessions.slice(0, 5).map(session => (
              <span key={session.id} title={session.name}>
                <StatusDot status={session.status} size="sm" />
              </span>
            ))}
            {filteredSessions.length > 5 && (
              <span className={styles.moreIndicator}>+{filteredSessions.length - 5}</span>
            )}
          </div>
        </div>
      ) : (
        <div className={cn(styles.sidebar, mobileSidebarOpen && styles.sidebarOpen)}>
          {/* Sidebar header: branding + collapse */}
          <div className={styles.sidebarHeader}>
            <div className={styles.sidebarBranding}>
              <div className={styles.brandIcon}>
                <Hammer className={styles.brandIconSvg} />
              </div>
              <div className={styles.brandText}>
                <span className={styles.brandTitle}>Völundr</span>
                <span className={styles.brandSubtitle}>The Crafting One</span>
              </div>
            </div>
            <button
              type="button"
              className={styles.toggleButton}
              onClick={() => setSidebarCollapsed(true)}
              aria-label="Collapse sidebar"
            >
              <ChevronLeft className={styles.toggleIcon} />
            </button>
          </div>

          {/* New session + search */}
          <div className={styles.searchRow}>
            <button
              type="button"
              className={styles.newSessionButton}
              onClick={() => setShowLaunchWizard(true)}
              title="New Session"
            >
              <Plus className={styles.newSessionIcon} />
            </button>
            <SearchInput value={searchQuery} onChange={setSearchQuery} placeholder="Search..." />
          </div>

          {/* View toggle + filter */}
          <div className={styles.filterRow}>
            <button
              type="button"
              className={styles.viewToggle}
              onClick={() => setCompactCards(!compactCards)}
              title={compactCards ? 'Expanded view' : 'Compact view'}
            >
              {compactCards ? (
                <LayoutGrid className={styles.viewToggleIcon} />
              ) : (
                <List className={styles.viewToggleIcon} />
              )}
            </button>
            <select
              className={styles.statusSelect}
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value)}
            >
              {STATUS_OPTIONS.map(opt => (
                <option key={opt} value={opt}>
                  {opt.charAt(0).toUpperCase() + opt.slice(1)}
                </option>
              ))}
            </select>
          </div>

          {/* Session list — grouped by repository */}
          <div className={styles.sessionsList}>
            <SessionGroupList
              sessions={filteredSessions}
              searchQuery={searchQuery}
              renderSession={session => (
                <div
                  key={session.id}
                  className={cn(
                    styles.sessionCardWrapper,
                    effectiveSelectedSession?.id === session.id && styles.selected
                  )}
                  onClick={() => {
                    setSelectedSession(session);
                    setMobileSidebarOpen(false);
                  }}
                >
                  <SessionCard
                    session={
                      liveChatCount !== null && effectiveSelectedSession?.id === session.id
                        ? { ...session, messageCount: liveChatCount }
                        : session
                    }
                    model={models[session.model]}
                    compact={compactCards}
                  />
                </div>
              )}
            />
          </div>

          {/* Archive all stopped + archived section */}
          <div className={styles.archivedSection}>
            <div
              className={styles.archivedToggle}
              role="button"
              tabIndex={0}
              onClick={() => setArchivedCollapsed(!archivedCollapsed)}
              onKeyDown={e => {
                if (e.key === 'Enter' || e.key === ' ') setArchivedCollapsed(!archivedCollapsed);
              }}
            >
              <div className={styles.archivedToggleLeft}>
                <Archive className={styles.archivedToggleIcon} />
                <span className={styles.archivedToggleTitle}>Archived</span>
                {archivedSessions.length > 0 && (
                  <span className={styles.archivedCount}>{archivedSessions.length}</span>
                )}
              </div>
              <div className={styles.archivedChevron}>
                {archivedCollapsed ? (
                  <ChevronRight className={styles.archivedChevronIcon} />
                ) : (
                  <ChevronDown className={styles.archivedChevronIcon} />
                )}
              </div>
            </div>

            {!archivedCollapsed && (
              <div className={styles.archivedContent}>
                {stoppedSessionCount > 0 && (
                  <div className={styles.archivedItem}>
                    <button
                      type="button"
                      className={styles.archiveAllButton}
                      onClick={handleArchiveAllStopped}
                    >
                      <Archive className={styles.archiveAllButtonIcon} />
                      Archive All Stopped ({stoppedSessionCount})
                    </button>
                  </div>
                )}
                {archivedSessions.map(session => (
                  <div key={session.id} className={styles.archivedItem}>
                    <div className={styles.archivedItemInfo}>
                      <span className={styles.archivedItemName}>{session.name}</span>
                      <span className={styles.archivedItemMeta}>
                        {getSourceLabel(session.source)} &middot;{' '}
                        {formatArchivedDate(session.archivedAt)}
                      </span>
                    </div>
                    <button
                      type="button"
                      className={styles.restoreButton}
                      onClick={() => handleRestoreSession(session.id)}
                    >
                      <RotateCcw className={styles.restoreButtonIcon} />
                      Restore
                    </button>
                  </div>
                ))}
                {archivedSessions.length === 0 && (
                  <div className={styles.archivedItem}>
                    <span className={styles.archivedItemMeta}>No archived sessions</span>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Forge stats footer */}
          <div className={styles.forgeStats} data-collapsed={statsCollapsed}>
            <button
              type="button"
              className={styles.forgeStatsToggle}
              onClick={() => setStatsCollapsed(!statsCollapsed)}
            >
              <div className={styles.forgeStatsLeft}>
                <Hammer className={styles.forgeStatsIcon} />
                <span className={styles.forgeStatsTitle}>Forge Stats</span>
              </div>
              {statsCollapsed && (
                <div className={styles.inlineStats}>
                  <span className={styles.inlineStat}>
                    <Activity className={styles.inlineStatIcon} data-color="emerald" />
                    <span className={styles.inlineStatValue}>{stats.activeSessions}</span>
                  </span>
                  <span className={styles.inlineStatSep} />
                  <span className={styles.inlineStat}>
                    <BarChart3 className={styles.inlineStatIcon} data-color="cyan" />
                    <span className={styles.inlineStatValue}>
                      {formatTokens(stats.tokensToday)}
                    </span>
                  </span>
                  <span className={styles.inlineStatSep} />
                  <span className={styles.inlineStat}>
                    <DollarSign className={styles.inlineStatIcon} data-color="amber" />
                    <span className={styles.inlineStatValue}>${stats.costToday.toFixed(2)}</span>
                  </span>
                </div>
              )}
              <div className={styles.forgeStatsChevron}>
                {statsCollapsed ? (
                  <ChevronRight className={styles.forgeStatsChevronIcon} />
                ) : (
                  <ChevronDown className={styles.forgeStatsChevronIcon} />
                )}
              </div>
            </button>

            {!statsCollapsed && (
              <div className={styles.forgeStatsContent}>
                <div className={styles.metricsGrid}>
                  <MetricCard
                    label="Active"
                    value={stats.activeSessions}
                    subtext="sessions"
                    icon={Activity}
                    iconColor="emerald"
                  />
                  <MetricCard
                    label="Total"
                    value={stats.totalSessions}
                    subtext="sessions"
                    icon={Database}
                    iconColor="purple"
                  />
                  <MetricCard
                    label="Tokens"
                    value={formatTokens(stats.tokensToday)}
                    subtext="today"
                    icon={BarChart3}
                    iconColor="cyan"
                  />
                  <MetricCard
                    label="Local"
                    value={formatTokens(stats.localTokens)}
                    subtext="GPU"
                    icon={Zap}
                    iconColor="emerald"
                  />
                  <MetricCard
                    label="Cloud"
                    value={formatTokens(stats.cloudTokens)}
                    subtext="API"
                    icon={Server}
                    iconColor="purple"
                  />
                  <MetricCard
                    label="Cost"
                    value={`$${stats.costToday.toFixed(2)}`}
                    subtext="today"
                    icon={DollarSign}
                    iconColor="amber"
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Mobile sidebar backdrop */}
      {mobileSidebarOpen && (
        <div className={styles.sidebarBackdrop} onClick={() => setMobileSidebarOpen(false)} />
      )}

      {/* ═══════════════ MAIN PANEL ═══════════════ */}
      {effectiveSelectedSession ? (
        <div className={styles.mainPanel}>
          {/* Mobile top bar with hamburger */}
          <div className={styles.mobileTopBar}>
            <button
              type="button"
              className={styles.menuButton}
              onClick={() => setMobileSidebarOpen(true)}
              aria-label="Open menu"
            >
              <Menu className={styles.menuButtonIcon} />
            </button>
            <span className={styles.mobileSessionName}>{effectiveSelectedSession.name}</span>
            <StatusBadge status={effectiveSelectedSession.status} />
          </div>

          {/* Session bar */}
          <div className={styles.sessionBar}>
            <div className={styles.sessionBarLeft}>
              {editingName ? (
                <form
                  className={styles.sessionNameForm}
                  onSubmit={async e => {
                    e.preventDefault();
                    const trimmed = editNameValue.trim();
                    if (trimmed && trimmed !== effectiveSelectedSession.name) {
                      await updateSession(effectiveSelectedSession.id, { name: trimmed });
                    }
                    setEditingName(false);
                  }}
                >
                  <input
                    className={styles.sessionNameInput}
                    value={editNameValue}
                    onChange={e => setEditNameValue(e.target.value)}
                    onBlur={() => setEditingName(false)}
                    onKeyDown={e => {
                      if (e.key === 'Escape') setEditingName(false);
                    }}
                    autoFocus
                    maxLength={63}
                  />
                  <button
                    type="submit"
                    className={styles.sessionNameSave}
                    onMouseDown={e => e.preventDefault()}
                  >
                    <Check className={styles.sessionNameIcon} />
                  </button>
                </form>
              ) : (
                <button
                  type="button"
                  className={styles.sessionNameBtn}
                  onClick={() => {
                    setEditNameValue(effectiveSelectedSession.name);
                    setEditingName(true);
                  }}
                >
                  <span className={styles.sessionName}>{effectiveSelectedSession.name}</span>
                  <Pencil className={styles.sessionNameEditIcon} />
                </button>
              )}
              <StatusBadge status={effectiveSelectedSession.status} />
              {effectiveSelectedSession.trackerIssue && (
                <a
                  href={effectiveSelectedSession.trackerIssue.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={styles.ticketLink}
                  title={effectiveSelectedSession.trackerIssue.title}
                >
                  {effectiveSelectedSession.trackerIssue.identifier}
                  <ExternalLink className={styles.ticketLinkIcon} />
                </a>
              )}
              <span className={styles.sessionBarSep} />
              <div className={styles.repoInfo}>
                <FolderGit2 className={styles.repoInfoIcon} />
                <span>{getSourceLabel(effectiveSelectedSession.source)}</span>
                {isGitSource(effectiveSelectedSession.source) && (
                  <>
                    <span className={styles.branchArrow}>&rarr;</span>
                    <span className={styles.branchName}>
                      {getBranch(effectiveSelectedSession.source)}
                    </span>
                  </>
                )}
              </div>
              {selectedModel && (
                <span
                  className={styles.modelBadge}
                  style={{ '--model-color': selectedModel.color } as React.CSSProperties}
                >
                  {isLocal ? (
                    <Zap className={styles.modelBadgeIcon} />
                  ) : (
                    <Server className={styles.modelBadgeIcon} />
                  )}
                  {selectedModel.name}
                </span>
              )}
            </div>
            <div className={styles.sessionBarRight}>
              {effectiveSelectedSession.status === 'running' ? (
                <button type="button" className={styles.stopButton} onClick={handleStopSession}>
                  <Square className={styles.actionBtnIcon} />
                  Stop
                </button>
              ) : (
                <button type="button" className={styles.startButton} onClick={handleResumeSession}>
                  <Play className={styles.actionBtnIcon} />
                  Start
                </button>
              )}
              <button
                type="button"
                className={styles.archiveButton}
                onClick={handleArchiveSession}
                title="Archive session"
              >
                <Archive className={styles.archiveButtonIcon} />
              </button>
              <button
                type="button"
                className={styles.deleteButton}
                onClick={handleDeleteSession}
                title="Delete session"
              >
                <Trash2 className={styles.deleteButtonIcon} />
              </button>
            </div>
          </div>

          {/* Tab bar */}
          <div className={styles.tabBar}>
            {tabs.map(tab => (
              <div key={tab.id} className={styles.tabWrapper}>
                <button
                  type="button"
                  className={cn(styles.tab, activeTab === tab.id && styles.active)}
                  onClick={() => setActiveTab(tab.id)}
                >
                  <tab.icon className={styles.tabIcon} />
                  {tab.label}
                  {tab.id === 'diffs' && chronicle && chronicle.files.length > 0 && (
                    <span className={styles.tabBadge}>{chronicle.files.length}</span>
                  )}
                </button>
                {(tab.id === 'chat' || tab.id === 'terminal' || tab.id === 'code') &&
                  effectiveSelectedSession?.status === 'running' && (
                    <button
                      type="button"
                      className={styles.popoutButton}
                      onClick={() => handlePopout(tab.id as 'chat' | 'terminal' | 'code')}
                      title={`Open ${tab.label} in new window`}
                    >
                      <Maximize2 className={styles.popoutIcon} />
                    </button>
                  )}
              </div>
            ))}
          </div>

          {/* Tab content */}
          <div className={styles.tabContent}>
            {activeTab === 'chat' &&
              (isSessionReady ? (
                <SessionChat
                  url={chatWsUrl}
                  className={styles.tabPanel}
                  onMessageCountChange={handleChatMessageCount}
                  sessionHost={sessionHost}
                  chatEndpoint={chatEndpoint}
                />
              ) : isSessionBooting(effectiveSelectedSession.status) ||
                (isRunning && !connectionVerified) ? (
                <SessionStartingIndicator className={styles.tabPanel} />
              ) : (
                <div className={styles.emptyState}>
                  <MessageSquare className={styles.emptyIcon} />
                  <p>Start the session to chat</p>
                </div>
              ))}

            {activeTab === 'terminal' &&
              (isSessionReady ? (
                <SessionTerminal url={terminalWsUrl} className={styles.tabPanel} />
              ) : isSessionBooting(effectiveSelectedSession.status) ||
                (isRunning && !connectionVerified) ? (
                <SessionStartingIndicator className={styles.tabPanel} />
              ) : (
                <div className={styles.emptyState}>
                  <Terminal className={styles.emptyIcon} />
                  <p>Start the session to access terminal</p>
                </div>
              ))}

            {activeTab === 'code' &&
              !isSessionReady &&
              (isSessionBooting(effectiveSelectedSession.status) ||
              (isRunning && !connectionVerified) ? (
                <SessionStartingIndicator className={styles.tabPanel} />
              ) : (
                <div className={styles.emptyState}>
                  <Code className={styles.emptyIcon} />
                  <p>Start the session to access IDE</p>
                </div>
              ))}
            {isSessionReady && codeTabEnabled && (
              <Suspense fallback={null}>
                <EditorPanel
                  hostname={effectiveSelectedSession.hostname ?? null}
                  sessionId={effectiveSelectedSession.id}
                  codeEndpoint={effectiveSelectedSession.codeEndpoint}
                  className={styles.tabPanel}
                  hidden={activeTab !== 'code'}
                />
              </Suspense>
            )}

            {activeTab === 'files' && (
              <FileManager chatEndpoint={chatEndpoint} className={styles.tabPanel} />
            )}

            {activeTab === 'diffs' && (
              <SessionDiffs
                sessionId={effectiveSelectedSession.id}
                chronicle={chronicle}
                chronicleLoading={chronicleLoading}
                onFetchChronicle={getChronicle}
                liveFiles={liveFiles}
                liveFilesLoading={liveFilesLoading}
                onFetchFiles={fetchFiles}
                diff={diff}
                diffLoading={diffLoading}
                diffError={diffError}
                selectedFile={selectedFile}
                diffBase={diffBase}
                onSelectFile={selectFile}
                onDiffBaseChange={setDiffBase}
                pendingDiffFile={pendingDiffFile}
                onPendingDiffConsumed={() => setPendingDiffFile(null)}
                className={styles.tabPanel}
              />
            )}

            {activeTab === 'chronicles' && (
              <SessionChronicles
                session={effectiveSelectedSession}
                sessionId={effectiveSelectedSession.id}
                sessionStatus={effectiveSelectedSession.status}
                chronicle={chronicle}
                loading={chronicleLoading}
                onFetch={getChronicle}
                className={styles.tabPanel}
                repoUrl={sessionRepoUrl}
                branch={getBranch(effectiveSelectedSession.source)}
                pullRequest={pullRequest}
                prLoading={prLoading}
                prCreating={prCreating}
                prMerging={prMerging}
                onFetchPR={fetchPullRequest}
                onCreatePR={createPullRequest}
                onMergePR={mergePullRequest}
                onRefreshCI={refreshCIStatus}
                trackerIssue={effectiveSelectedSession.trackerIssue}
                onTrackerStatusChange={updateTrackerIssueStatus}
                onNavigateToDiff={(filePath: string) => {
                  setPendingDiffFile(filePath);
                  setActiveTab('diffs');
                }}
              />
            )}

            {activeTab === 'logs' &&
              (() => {
                const effectiveLogs = sessionHostLogs.length > 0 ? sessionHostLogs : logs;
                const isLoadingLogs =
                  sessionHostLogs.length > 0 ? sessionHostLogLoading : logLoading;

                return (
                  <div className={styles.logsContent}>
                    {isLoadingLogs && effectiveLogs.length === 0 ? (
                      <div className={styles.loadingMessages}>Loading logs...</div>
                    ) : effectiveLogs.length === 0 ? (
                      <div className={styles.emptyMessages}>
                        <FileText className={styles.emptyIcon} />
                        <p>
                          {effectiveSelectedSession.status !== 'running'
                            ? 'Start the session to view logs'
                            : 'No logs available'}
                        </p>
                      </div>
                    ) : (
                      effectiveLogs.map(log => (
                        <div key={log.id} className={styles.logLine}>
                          {formatLogTimestamp(log.timestamp)}{' '}
                          <span
                            className={
                              styles[`log${log.level.charAt(0).toUpperCase()}${log.level.slice(1)}`]
                            }
                          >
                            {log.level.toUpperCase()}
                          </span>{' '}
                          [{log.source}] {log.message}
                        </div>
                      ))
                    )}
                  </div>
                );
              })()}
          </div>
        </div>
      ) : (
        <div className={styles.emptyMain}>
          {/* Mobile top bar with hamburger (empty state) */}
          <div className={styles.mobileTopBar}>
            <button
              type="button"
              className={styles.menuButton}
              onClick={() => setMobileSidebarOpen(true)}
              aria-label="Open menu"
            >
              <Menu className={styles.menuButtonIcon} />
            </button>
            <span className={styles.mobileSessionName}>Völundr</span>
          </div>
          <Hammer className={styles.emptyMainIcon} />
          <p className={styles.emptyMainText}>Select a session to view details</p>
          <button
            type="button"
            className={styles.newButton}
            onClick={() => setShowLaunchWizard(true)}
          >
            <Plus className={styles.actionBtnIcon} />
            New Session
          </button>
        </div>
      )}

      {/* ═══════════════ MODALS ═══════════════ */}
      {showLaunchWizard && (
        <Modal
          isOpen={true}
          onClose={() => {
            setShowLaunchWizard(false);
            setLaunchError(null);
          }}
          title="Launch Session"
          size="xl"
        >
          <LaunchWizard
            templates={templates}
            presets={presets}
            repos={repos}
            models={models}
            availableMcpServers={availableMcpServers}
            availableSecrets={availableSecrets}
            service={volundrService}
            onLaunch={handleLaunchSession}
            onSaveTemplate={async t => {
              await saveTemplate(t);
            }}
            onSavePreset={savePreset}
            isLaunching={isLaunching}
            searchTrackerIssues={searchTrackerIssues}
          />
          {launchError && (
            <div className={styles.launchError}>
              {launchError}
              <button
                type="button"
                className={styles.launchErrorDismiss}
                onClick={() => setLaunchError(null)}
              >
                Dismiss
              </button>
            </div>
          )}
        </Modal>
      )}

      <DeleteSessionDialog
        isOpen={showDeleteDialog}
        sessionName={effectiveSelectedSession?.name ?? ''}
        isManual={false}
        isLocalStorage={effectiveSelectedSession?.source?.type === 'local_mount'}
        onConfirm={handleDeleteConfirm}
        onCancel={handleDeleteCancel}
      />
    </div>
  );
}
