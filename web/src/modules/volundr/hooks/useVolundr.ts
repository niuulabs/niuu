import { useState, useEffect, useCallback, useRef } from 'react';
import type {
  VolundrSession,
  VolundrStats,
  VolundrModel,
  VolundrRepo,
  VolundrMessage,
  VolundrLog,
  SessionChronicle,
  SessionSource,
  PullRequest,
  VolundrPreset,
  VolundrTemplate,
  McpServerConfig,
  TrackerIssue,
  TrackerIssueStatus,
} from '@/modules/volundr/models';
import { volundrService } from '@/modules/volundr/adapters';

interface UseVolundrResult {
  stats: VolundrStats | null;
  sessions: VolundrSession[];
  activeSessions: VolundrSession[];
  models: Record<string, VolundrModel>;
  repos: VolundrRepo[];
  templates: VolundrTemplate[];
  presets: VolundrPreset[];
  availableMcpServers: McpServerConfig[];
  availableSecrets: string[];
  loading: boolean;
  error: Error | null;
  getSession: (id: string) => Promise<VolundrSession | null>;
  refreshSession: (id: string) => Promise<void>;
  markSessionRunning: (id: string) => void;
  startSession: (config: {
    name: string;
    source: SessionSource;
    model: string;
    templateName?: string;
    definition?: string;
    taskType?: string;
    trackerIssue?: TrackerIssue;
    terminalRestricted?: boolean;
    workspaceId?: string;
    credentialNames?: string[];
    integrationIds?: string[];
    resourceConfig?: Record<string, string | undefined>;
    systemPrompt?: string;
    initialPrompt?: string;
  }) => Promise<VolundrSession>;
  connectSession: (config: { name: string; hostname: string }) => Promise<VolundrSession>;
  updateSession: (sessionId: string, updates: { name?: string }) => Promise<VolundrSession>;
  stopSession: (sessionId: string) => Promise<void>;
  resumeSession: (sessionId: string) => Promise<void>;
  deleteSession: (sessionId: string, cleanup?: string[]) => Promise<void>;
  archiveSession: (sessionId: string) => Promise<void>;
  restoreSession: (sessionId: string) => Promise<void>;
  archivedSessions: VolundrSession[];
  archiveAllStopped: () => Promise<void>;
  refresh: () => Promise<void>;
  saveTemplate: (template: VolundrTemplate) => Promise<VolundrTemplate>;
  savePreset: (
    preset: Omit<VolundrPreset, 'id' | 'createdAt' | 'updatedAt'> & { id?: string }
  ) => Promise<VolundrPreset>;
  deletePreset: (id: string) => Promise<void>;
  createSecret: (
    name: string,
    data: Record<string, string>
  ) => Promise<{ name: string; keys: string[] }>;
  // Message methods
  messages: VolundrMessage[];
  messageLoading: boolean;
  getMessages: (sessionId: string) => Promise<void>;
  sendMessage: (sessionId: string, content: string) => Promise<VolundrMessage>;
  // Log methods
  logs: VolundrLog[];
  logLoading: boolean;
  getLogs: (sessionId: string) => Promise<void>;
  // Code server
  openCodeServer: (sessionId: string) => Promise<void>;
  getCodeServerUrl: (sessionId: string) => Promise<string | null>;
  // Chronicle methods
  chronicle: SessionChronicle | null;
  chronicleLoading: boolean;
  getChronicle: (sessionId: string) => Promise<void>;
  // Pull request methods
  pullRequest: PullRequest | null;
  prLoading: boolean;
  prCreating: boolean;
  prMerging: boolean;
  fetchPullRequest: (repoUrl: string, branch: string) => Promise<void>;
  createPullRequest: (sessionId: string, title?: string, targetBranch?: string) => Promise<void>;
  mergePullRequest: (prNumber: number, repoUrl: string) => Promise<void>;
  refreshCIStatus: (prNumber: number, repoUrl: string, branch: string) => Promise<void>;
  // Tracker issue methods
  searchTrackerIssues: (query: string) => Promise<TrackerIssue[]>;
  updateTrackerIssueStatus: (issueId: string, status: TrackerIssueStatus) => Promise<void>;
}

export function useVolundr(): UseVolundrResult {
  const [stats, setStats] = useState<VolundrStats | null>(null);
  const [sessions, setSessions] = useState<VolundrSession[]>([]);
  const [models, setModels] = useState<Record<string, VolundrModel>>({});
  const [repos, setRepos] = useState<VolundrRepo[]>([]);
  const [templates, setTemplates] = useState<VolundrTemplate[]>([]);
  const [presets, setPresets] = useState<VolundrPreset[]>([]);
  const [availableMcpServers, setAvailableMcpServers] = useState<McpServerConfig[]>([]);
  const [availableSecrets, setAvailableSecrets] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [messages, setMessages] = useState<VolundrMessage[]>([]);
  const [messageLoading, setMessageLoading] = useState(false);
  const [logs, setLogs] = useState<VolundrLog[]>([]);
  const [logLoading, setLogLoading] = useState(false);
  const [chronicle, setChronicle] = useState<SessionChronicle | null>(null);
  const [chronicleLoading, setChronicleLoading] = useState(false);
  const chronicleUnsubRef = useRef<(() => void) | null>(null);
  const [pullRequest, setPullRequest] = useState<PullRequest | null>(null);
  const [prLoading, setPrLoading] = useState(false);
  const [prCreating, setPrCreating] = useState(false);
  const [prMerging, setPrMerging] = useState(false);
  const [archivedSessions, setArchivedSessions] = useState<VolundrSession[]>([]);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [
        statsData,
        sessionsData,
        modelsData,
        reposData,
        templatesData,
        presetsData,
        mcpServersData,
        secretsData,
        archivedData,
      ] = await Promise.all([
        volundrService.getStats(),
        volundrService.getSessions(),
        volundrService.getModels(),
        volundrService.getRepos(),
        volundrService.getTemplates(),
        volundrService.getPresets(),
        volundrService.getAvailableMcpServers(),
        volundrService.getAvailableSecrets(),
        volundrService.listArchivedSessions(),
      ]);
      setStats(statsData);
      setSessions(sessionsData);
      setModels(modelsData);
      setRepos(reposData);
      setTemplates(templatesData);
      setPresets(presetsData);
      setAvailableMcpServers(mcpServersData);
      setAvailableSecrets(secretsData);
      setArchivedSessions(archivedData);
    } catch (err) {
      setError(err instanceof Error ? err : new Error('Failed to fetch Völundr data'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();

    const unsubSessions = volundrService.subscribe(newSessions => {
      setSessions(newSessions);
      // Recompute session-derived stats as a fallback when the backend
      // only emits session_updated without a corresponding stats_updated.
      setStats(prev => {
        if (!prev) {
          return prev;
        }
        const active = newSessions.filter(s => s.status === 'running').length;
        const total = newSessions.length;
        const tokens = newSessions.reduce((sum, s) => sum + s.tokensUsed, 0);
        return { ...prev, activeSessions: active, totalSessions: total, tokensToday: tokens };
      });
    });

    const unsubStats = volundrService.subscribeStats(newStats => {
      setStats(newStats);
    });

    return () => {
      unsubSessions();
      unsubStats();
      if (chronicleUnsubRef.current) {
        chronicleUnsubRef.current();
        chronicleUnsubRef.current = null;
      }
    };
  }, [fetchData]);

  const activeSessions = sessions.filter(s => s.status === 'running');

  const getSession = useCallback(async (id: string) => {
    return volundrService.getSession(id);
  }, []);

  const refreshSession = useCallback(async (id: string) => {
    const updated = await volundrService.getSession(id);
    if (!updated) {
      return;
    }
    setSessions(prev => prev.map(s => (s.id === id ? updated : s)));
  }, []);

  const markSessionRunning = useCallback((id: string) => {
    setSessions(prev => prev.map(s => (s.id === id ? { ...s, status: 'running' as const } : s)));
  }, []);

  const startSession = useCallback(
    async (config: {
      name: string;
      source: SessionSource;
      model: string;
      templateName?: string;
      taskType?: string;
      trackerIssue?: TrackerIssue;
      terminalRestricted?: boolean;
      workspaceId?: string;
      credentialNames?: string[];
      integrationIds?: string[];
      resourceConfig?: Record<string, string | undefined>;
      systemPrompt?: string;
      initialPrompt?: string;
    }) => {
      return volundrService.startSession(config);
    },
    []
  );

  const connectSession = useCallback(async (config: { name: string; hostname: string }) => {
    return volundrService.connectSession(config);
  }, []);

  const updateSession = useCallback(async (sessionId: string, updates: { name?: string }) => {
    const updated = await volundrService.updateSession(sessionId, updates);
    setSessions(prev => prev.map(s => (s.id === sessionId ? updated : s)));
    return updated;
  }, []);

  const stopSession = useCallback(async (sessionId: string) => {
    await volundrService.stopSession(sessionId);
    setSessions(prev =>
      prev.map(s => (s.id === sessionId ? { ...s, status: 'stopped' as const } : s))
    );
  }, []);

  const resumeSession = useCallback(async (sessionId: string) => {
    await volundrService.resumeSession(sessionId);
    setSessions(prev =>
      prev.map(s => (s.id === sessionId ? { ...s, status: 'starting' as const } : s))
    );
  }, []);

  const deleteSession = useCallback(async (sessionId: string, cleanup?: string[]) => {
    await volundrService.deleteSession(sessionId, cleanup);
    setSessions(prev => prev.filter(s => s.id !== sessionId));
  }, []);

  const archiveSession = useCallback(async (sessionId: string) => {
    await volundrService.archiveSession(sessionId);
    setSessions(prev => prev.filter(s => s.id !== sessionId));
    const archived = await volundrService.listArchivedSessions();
    setArchivedSessions(archived);
  }, []);

  const restoreSession = useCallback(async (sessionId: string) => {
    await volundrService.restoreSession(sessionId);
    const [updatedSessions, updatedArchived] = await Promise.all([
      volundrService.getSessions(),
      volundrService.listArchivedSessions(),
    ]);
    setSessions(updatedSessions);
    setArchivedSessions(updatedArchived);
  }, []);

  const archiveAllStopped = useCallback(async () => {
    const stopped = sessions.filter(s => s.status === 'stopped');
    for (const s of stopped) {
      await volundrService.archiveSession(s.id);
    }
    setSessions(prev => prev.filter(s => s.status !== 'stopped'));
    const archived = await volundrService.listArchivedSessions();
    setArchivedSessions(archived);
  }, [sessions]);

  const saveTemplate = useCallback(async (template: VolundrTemplate) => {
    const saved = await volundrService.saveTemplate(template);
    setTemplates(prev => {
      const idx = prev.findIndex(t => t.name === saved.name);
      if (idx !== -1) {
        const updated = [...prev];
        updated[idx] = saved;
        return updated;
      }
      return [...prev, saved];
    });
    return saved;
  }, []);

  const savePreset = useCallback(
    async (preset: Omit<VolundrPreset, 'id' | 'createdAt' | 'updatedAt'> & { id?: string }) => {
      const saved = await volundrService.savePreset(preset);
      setPresets(prev => {
        const idx = prev.findIndex(p => p.id === saved.id);
        if (idx !== -1) {
          const updated = [...prev];
          updated[idx] = saved;
          return updated;
        }
        return [...prev, saved];
      });
      return saved;
    },
    []
  );

  const deletePreset = useCallback(async (id: string) => {
    await volundrService.deletePreset(id);
    setPresets(prev => prev.filter(p => p.id !== id));
  }, []);

  const createSecret = useCallback(async (name: string, data: Record<string, string>) => {
    const result = await volundrService.createSecret(name, data);
    // Refresh available secrets
    setAvailableSecrets(prev => (prev.includes(name) ? prev : [...prev, name]));
    return result;
  }, []);

  const getMessages = useCallback(async (sessionId: string) => {
    setMessageLoading(true);
    try {
      const data = await volundrService.getMessages(sessionId);
      setMessages(data);
    } finally {
      setMessageLoading(false);
    }
  }, []);

  const sendMessage = useCallback(async (sessionId: string, content: string) => {
    // Optimistic update: add user message immediately
    const userMessage: VolundrMessage = {
      id: `temp-${Date.now()}`,
      sessionId,
      role: 'user',
      content,
      timestamp: Date.now(),
    };
    setMessages(prev => [...prev, userMessage]);

    const response = await volundrService.sendMessage(sessionId, content);

    // Refresh messages to get accurate data
    const updatedMessages = await volundrService.getMessages(sessionId);
    setMessages(updatedMessages);

    return response;
  }, []);

  const getLogs = useCallback(async (sessionId: string) => {
    setLogLoading(true);
    try {
      const data = await volundrService.getLogs(sessionId);
      setLogs(data);
    } finally {
      setLogLoading(false);
    }
  }, []);

  const getCodeServerUrl = useCallback(async (sessionId: string): Promise<string | null> => {
    return volundrService.getCodeServerUrl(sessionId);
  }, []);

  const getChronicle = useCallback(async (sessionId: string) => {
    // Tear down previous subscription
    if (chronicleUnsubRef.current) {
      chronicleUnsubRef.current();
      chronicleUnsubRef.current = null;
    }

    setChronicleLoading(true);
    try {
      const data = await volundrService.getChronicle(sessionId);
      setChronicle(data);
    } finally {
      setChronicleLoading(false);
    }

    // Subscribe to live updates for this session
    chronicleUnsubRef.current = volundrService.subscribeChronicle(
      sessionId,
      (update: SessionChronicle) => {
        setChronicle(prev => {
          if (!prev) {
            return update;
          }
          return {
            events: [...prev.events, ...update.events],
            files: update.files,
            commits: update.commits,
            tokenBurn: update.tokenBurn,
          };
        });
      }
    );
  }, []);

  const openCodeServer = useCallback(async (sessionId: string) => {
    const url = await volundrService.getCodeServerUrl(sessionId);
    if (url) {
      window.open(url, '_blank');
    }
  }, []);

  const fetchPullRequest = useCallback(async (repoUrl: string, branch: string) => {
    setPrLoading(true);
    try {
      const prs = await volundrService.getPullRequests(repoUrl, 'open');
      const match = prs.find(pr => pr.sourceBranch === branch);
      setPullRequest(match ?? null);
    } catch {
      setPullRequest(null);
    } finally {
      setPrLoading(false);
    }
  }, []);

  const createPullRequest = useCallback(
    async (sessionId: string, title?: string, targetBranch?: string) => {
      setPrCreating(true);
      try {
        const pr = await volundrService.createPullRequest(sessionId, title, targetBranch);
        setPullRequest(pr);
      } finally {
        setPrCreating(false);
      }
    },
    []
  );

  const mergePullRequest = useCallback(async (prNumber: number, repoUrl: string) => {
    setPrMerging(true);
    try {
      await volundrService.mergePullRequest(prNumber, repoUrl);
      setPullRequest(prev => (prev ? { ...prev, status: 'merged' } : null));
    } finally {
      setPrMerging(false);
    }
  }, []);

  const refreshCIStatus = useCallback(async (prNumber: number, repoUrl: string, branch: string) => {
    try {
      const status = await volundrService.getCIStatus(prNumber, repoUrl, branch);
      setPullRequest(prev => (prev ? { ...prev, ciStatus: status } : null));
    } catch {
      // Silently ignore CI status fetch failures
    }
  }, []);

  const searchTrackerIssues = useCallback(async (query: string): Promise<TrackerIssue[]> => {
    return volundrService.searchTrackerIssues(query);
  }, []);

  const updateTrackerIssueStatus = useCallback(
    async (issueId: string, status: TrackerIssueStatus) => {
      const updated = await volundrService.updateTrackerIssueStatus(issueId, status);
      // Update sessions that reference this issue
      setSessions(prev =>
        prev.map(s => (s.trackerIssue?.id === issueId ? { ...s, trackerIssue: updated } : s))
      );
    },
    []
  );

  return {
    stats,
    sessions,
    activeSessions,
    models,
    repos,
    templates,
    presets,
    availableMcpServers,
    availableSecrets,
    loading,
    error,
    getSession,
    refreshSession,
    markSessionRunning,
    startSession,
    connectSession,
    updateSession,
    stopSession,
    resumeSession,
    deleteSession,
    archiveSession,
    restoreSession,
    archivedSessions,
    archiveAllStopped,
    refresh: fetchData,
    saveTemplate,
    savePreset,
    deletePreset,
    createSecret,
    messages,
    messageLoading,
    getMessages,
    sendMessage,
    logs,
    logLoading,
    getLogs,
    openCodeServer,
    getCodeServerUrl,
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
    searchTrackerIssues,
    updateTrackerIssueStatus,
  };
}
