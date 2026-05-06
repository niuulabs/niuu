import type { IVolundrService } from '@/modules/volundr/ports';
import type {
  VolundrSession,
  VolundrStats,
  VolundrModel,
  VolundrRepo,
  VolundrMessage,
  VolundrLog,
  SessionChronicle,
  PullRequest,
  MergeResult,
  CIStatusValue,
  McpServer,
  McpServerConfig,
  VolundrPreset,
  VolundrTemplate,
  TrackerIssue,
  ProjectRepoMapping,
  VolundrIdentity,
  VolundrUser,
  VolundrTenant,
  VolundrCredential,
  IntegrationConnection,
  IntegrationTestResult,
  CatalogEntry,
  VolundrWorkspace,
  WorkspaceStatus,
  VolundrMember,
  VolundrProvisioningResult,
  AdminSettings,
  AdminStorageSettings,
} from '@/modules/volundr/models';
import { isSessionActive } from '@/modules/volundr/models';
import {
  mockVolundrSessions,
  mockArchivedSessions,
  mockVolundrStats,
  mockVolundrModels,
  mockVolundrRepos,
  mockVolundrMessages,
  mockVolundrLogs,
  mockVolundrChronicles,
  mockVolundrPullRequests,
  mockVolundrPresets,
  mockVolundrTemplates,
  mockTrackerIssues,
  mockProjectRepoMappings,
  mockVolundrMcpServers,
  mockAvailableMcpServers,
  mockAvailableSecrets,
} from './data';

/**
 * Mock implementation of IVolundrService
 * Returns canned data for development and testing
 */
export class MockVolundrService implements IVolundrService {
  private sessions: VolundrSession[] = mockVolundrSessions.map(s => ({ ...s }));
  private stats: VolundrStats = { ...mockVolundrStats };
  private models: Record<string, VolundrModel> = { ...mockVolundrModels };
  private messages: Record<string, VolundrMessage[]> = JSON.parse(
    JSON.stringify(mockVolundrMessages)
  );
  private logs: Record<string, VolundrLog[]> = JSON.parse(JSON.stringify(mockVolundrLogs));
  private subscribers: Set<(sessions: VolundrSession[]) => void> = new Set();
  private statsSubscribers: Set<(stats: VolundrStats) => void> = new Set();
  private archivedSessions: VolundrSession[] = mockArchivedSessions.map(s => ({ ...s }));
  private pullRequests: PullRequest[] = mockVolundrPullRequests.map(pr => ({ ...pr }));
  private messageSubscribers: Map<string, Set<(message: VolundrMessage) => void>> = new Map();
  private logSubscribers: Map<string, Set<(log: VolundrLog) => void>> = new Map();
  private templates: VolundrTemplate[] = JSON.parse(JSON.stringify(mockVolundrTemplates));
  private presets: VolundrPreset[] = JSON.parse(JSON.stringify(mockVolundrPresets));
  private transitionTimers: Map<string, ReturnType<typeof setTimeout>> = new Map();

  /**
   * Simulate the backend two-phase transition: starting → provisioning → running.
   */
  private simulateTransition(sessionId: string): void {
    const outer = setTimeout(() => {
      const session = this.sessions.find(s => s.id === sessionId);
      if (!session || session.status !== 'starting') return;
      session.status = 'provisioning';
      this.notifySubscribers();
      const inner = setTimeout(() => {
        this.transitionTimers.delete(sessionId);
        const s = this.sessions.find(s => s.id === sessionId);
        if (!s || s.status !== 'provisioning') return;
        s.status = 'running';
        s.hostname = s.hostname || `skuld-${sessionId}.local`;
        this.notifySubscribers();
      }, 3000);
      this.transitionTimers.set(sessionId, inner);
    }, 2000);
    this.transitionTimers.set(sessionId, outer);
  }

  private cancelTransition(sessionId: string): void {
    const timer = this.transitionTimers.get(sessionId);
    if (timer) {
      clearTimeout(timer);
      this.transitionTimers.delete(sessionId);
    }
  }

  async getSessionDefinitions(): Promise<import('@/modules/volundr/models').SessionDefinition[]> {
    return [
      {
        key: 'skuldClaude',
        displayName: 'Claude Code',
        description: 'Anthropic Claude CLI agent',
        labels: ['claude', 'anthropic'],
        defaultModel: 'claude-sonnet',
      },
      {
        key: 'skuldCodex',
        displayName: 'Codex',
        description: 'OpenAI Codex CLI agent',
        labels: ['codex', 'openai'],
        defaultModel: 'codex',
      },
    ];
  }

  async getSessions(): Promise<VolundrSession[]> {
    return this.sessions.map(s => ({ ...s }));
  }

  async getSession(id: string): Promise<VolundrSession | null> {
    const session = this.sessions.find(s => s.id === id);
    return session ? { ...session } : null;
  }

  async getActiveSessions(): Promise<VolundrSession[]> {
    return this.sessions.filter(s => s.status === 'running').map(s => ({ ...s }));
  }

  async getStats(): Promise<VolundrStats> {
    return { ...this.stats };
  }

  async getFeatures(): Promise<import('@/models').VolundrFeatures> {
    return { localMountsEnabled: true, fileManagerEnabled: true, miniMode: false };
  }

  async getModels(): Promise<Record<string, VolundrModel>> {
    return { ...this.models };
  }

  async getRepos(): Promise<VolundrRepo[]> {
    return mockVolundrRepos.map(r => ({ ...r }));
  }

  subscribe(callback: (sessions: VolundrSession[]) => void): () => void {
    this.subscribers.add(callback);
    return () => {
      this.subscribers.delete(callback);
    };
  }

  subscribeStats(callback: (stats: VolundrStats) => void): () => void {
    this.statsSubscribers.add(callback);
    // Immediately notify with current stats
    callback({ ...this.stats });
    return () => {
      this.statsSubscribers.delete(callback);
    };
  }

  async getTemplates(): Promise<VolundrTemplate[]> {
    return JSON.parse(JSON.stringify(this.templates));
  }

  async getTemplate(name: string): Promise<VolundrTemplate | null> {
    const template = this.templates.find(t => t.name === name);
    return template ? JSON.parse(JSON.stringify(template)) : null;
  }

  async saveTemplate(template: VolundrTemplate): Promise<VolundrTemplate> {
    const existingIndex = this.templates.findIndex(t => t.name === template.name);
    const saved = JSON.parse(JSON.stringify(template));
    if (existingIndex !== -1) {
      this.templates[existingIndex] = saved;
    } else {
      this.templates.push(saved);
    }
    return JSON.parse(JSON.stringify(saved));
  }

  async getPresets(): Promise<VolundrPreset[]> {
    return JSON.parse(JSON.stringify(this.presets));
  }

  async getPreset(id: string): Promise<VolundrPreset | null> {
    const preset = this.presets.find(p => p.id === id);
    return preset ? JSON.parse(JSON.stringify(preset)) : null;
  }

  async savePreset(
    preset: Omit<VolundrPreset, 'id' | 'createdAt' | 'updatedAt'> & { id?: string }
  ): Promise<VolundrPreset> {
    const now = new Date().toISOString();

    if (preset.id) {
      const existingIndex = this.presets.findIndex(p => p.id === preset.id);
      if (existingIndex !== -1) {
        const updated: VolundrPreset = {
          ...this.presets[existingIndex],
          ...preset,
          id: preset.id,
          updatedAt: now,
        };
        this.presets[existingIndex] = updated;
        return JSON.parse(JSON.stringify(updated));
      }
    }

    const created: VolundrPreset = {
      ...preset,
      id: `preset-${crypto.randomUUID().substring(0, 8)}`,
      createdAt: now,
      updatedAt: now,
    };
    this.presets.push(created);
    return JSON.parse(JSON.stringify(created));
  }

  async deletePreset(id: string): Promise<void> {
    this.presets = this.presets.filter(p => p.id !== id);
  }

  async getAvailableMcpServers(): Promise<McpServerConfig[]> {
    return mockAvailableMcpServers.map(s => ({ ...s, args: s.args ? [...s.args] : undefined }));
  }

  async getAvailableSecrets(): Promise<string[]> {
    return [...mockAvailableSecrets];
  }

  async createSecret(
    name: string,
    data: Record<string, string>
  ): Promise<{ name: string; keys: string[] }> {
    // Simulate creating a secret — just return the keys
    return { name, keys: Object.keys(data) };
  }

  async getClusterResources(): Promise<import('@/models').ClusterResourceInfo> {
    return {
      resourceTypes: [
        { name: 'cpu', resourceKey: 'cpu', displayName: 'CPU', unit: 'cores', category: 'compute' },
        {
          name: 'memory',
          resourceKey: 'memory',
          displayName: 'Memory',
          unit: 'bytes',
          category: 'compute',
        },
        {
          name: 'gpu',
          resourceKey: 'nvidia.com/gpu',
          displayName: 'GPU',
          unit: 'devices',
          category: 'accelerator',
        },
      ],
      nodes: [],
    };
  }

  async startSession(config: {
    name: string;
    source: import('@/models').SessionSource;
    model: string;
    templateName?: string;
    taskType?: string;
    definition?: string;
    trackerIssue?: TrackerIssue;
    terminalRestricted?: boolean;
    credentialNames?: string[];
    integrationIds?: string[];
    resourceConfig?: Record<string, string | undefined>;
    systemPrompt?: string;
    initialPrompt?: string;
  }): Promise<VolundrSession> {
    const newSession: VolundrSession = {
      id: `forge-${crypto.randomUUID().substring(0, 8)}`,
      name: config.name,
      source: config.source,
      status: 'starting',
      model: config.model,
      lastActive: Date.now(),
      messageCount: 0,
      tokensUsed: 0,
      taskType: config.taskType,
      trackerIssue: config.trackerIssue,
    };
    this.sessions.unshift(newSession);
    this.stats.totalSessions += 1;
    this.stats.activeSessions += 1;
    this.notifySubscribers();

    this.simulateTransition(newSession.id);

    return { ...newSession };
  }

  async connectSession(config: { name: string; hostname: string }): Promise<VolundrSession> {
    const newSession: VolundrSession = {
      id: `manual-${crypto.randomUUID().substring(0, 8)}`,
      name: config.name,
      source: { type: 'git', repo: '', branch: '' },
      status: 'starting',
      model: 'external',
      lastActive: Date.now(),
      messageCount: 0,
      tokensUsed: 0,
      origin: 'manual',
      hostname: config.hostname,
    };
    this.sessions.unshift(newSession);
    this.stats.totalSessions += 1;
    this.stats.activeSessions += 1;
    this.notifySubscribers();

    this.simulateTransition(newSession.id);

    return { ...newSession };
  }

  async updateSession(
    sessionId: string,
    updates: { name?: string; model?: string; branch?: string; tracker_issue_id?: string }
  ): Promise<VolundrSession> {
    const session = this.sessions.find(s => s.id === sessionId);
    if (!session) {
      throw new Error(`Session not found: ${sessionId}`);
    }
    if (updates.name) session.name = updates.name;
    if (updates.model) session.model = updates.model;
    this.notifySubscribers();
    return session;
  }

  async stopSession(sessionId: string): Promise<void> {
    const session = this.sessions.find(s => s.id === sessionId);
    if (session && isSessionActive(session.status)) {
      this.cancelTransition(sessionId);
      session.status = 'stopped';
      this.stats.activeSessions -= 1;
      this.notifySubscribers();
    }
  }

  async resumeSession(sessionId: string): Promise<void> {
    const session = this.sessions.find(s => s.id === sessionId);
    if (session && session.status === 'stopped') {
      session.status = 'starting';
      session.lastActive = Date.now();
      this.stats.activeSessions += 1;
      this.notifySubscribers();

      this.simulateTransition(sessionId);
    }
  }

  async deleteSession(sessionId: string, _cleanup: string[] = []): Promise<void> {
    const sessionIndex = this.sessions.findIndex(s => s.id === sessionId);
    if (sessionIndex === -1) {
      return;
    }

    const session = this.sessions[sessionIndex];
    if (isSessionActive(session.status)) {
      this.cancelTransition(sessionId);
      this.stats.activeSessions -= 1;
    }
    this.stats.totalSessions -= 1;
    this.sessions.splice(sessionIndex, 1);
    this.notifySubscribers();
  }

  async archiveSession(sessionId: string): Promise<void> {
    const sessionIndex = this.sessions.findIndex(s => s.id === sessionId);
    if (sessionIndex === -1) {
      return;
    }

    const session = this.sessions[sessionIndex];
    if (isSessionActive(session.status)) {
      this.cancelTransition(sessionId);
      this.stats.activeSessions -= 1;
    }
    this.stats.totalSessions -= 1;
    this.sessions.splice(sessionIndex, 1);

    session.status = 'archived';
    session.archivedAt = new Date();
    this.archivedSessions.unshift(session);
    this.notifySubscribers();
  }

  async restoreSession(sessionId: string): Promise<void> {
    const archiveIndex = this.archivedSessions.findIndex(s => s.id === sessionId);
    if (archiveIndex === -1) {
      return;
    }

    const session = this.archivedSessions[archiveIndex];
    this.archivedSessions.splice(archiveIndex, 1);

    session.status = 'stopped';
    session.archivedAt = undefined;
    this.sessions.unshift(session);
    this.stats.totalSessions += 1;
    this.notifySubscribers();
  }

  async listArchivedSessions(): Promise<VolundrSession[]> {
    return this.archivedSessions.map(s => ({ ...s }));
  }

  async getMessages(sessionId: string): Promise<VolundrMessage[]> {
    return (this.messages[sessionId] || []).map(m => ({ ...m }));
  }

  async sendMessage(sessionId: string, content: string): Promise<VolundrMessage> {
    const userMessage: VolundrMessage = {
      id: `msg-${Date.now()}-user`,
      sessionId,
      role: 'user',
      content,
      timestamp: Date.now(),
    };

    if (!this.messages[sessionId]) {
      this.messages[sessionId] = [];
    }
    this.messages[sessionId].push(userMessage);
    this.notifyMessageSubscribers(sessionId, userMessage);

    // Simulate assistant response after a short delay
    await new Promise(resolve => setTimeout(resolve, 100));

    const assistantMessage: VolundrMessage = {
      id: `msg-${Date.now()}-assistant`,
      sessionId,
      role: 'assistant',
      content: `I'll help you with that. Analyzing your request about "${content.slice(0, 50)}${content.length > 50 ? '...' : ''}"`,
      timestamp: Date.now(),
      tokensIn: Math.ceil(content.split(/\s+/).length * 1.3),
      tokensOut: 150,
      latency: 500,
    };

    this.messages[sessionId].push(assistantMessage);
    this.notifyMessageSubscribers(sessionId, assistantMessage);

    // Update session stats
    const session = this.sessions.find(s => s.id === sessionId);
    if (session) {
      session.messageCount += 2;
      session.tokensUsed += (assistantMessage.tokensIn || 0) + (assistantMessage.tokensOut || 0);
      session.lastActive = Date.now();
      this.notifySubscribers();
    }

    return { ...assistantMessage };
  }

  subscribeMessages(sessionId: string, callback: (message: VolundrMessage) => void): () => void {
    if (!this.messageSubscribers.has(sessionId)) {
      this.messageSubscribers.set(sessionId, new Set());
    }
    this.messageSubscribers.get(sessionId)!.add(callback);

    return () => {
      this.messageSubscribers.get(sessionId)?.delete(callback);
    };
  }

  async getLogs(sessionId: string, limit = 100): Promise<VolundrLog[]> {
    const sessionLogs = this.logs[sessionId] || [];
    return sessionLogs.slice(-limit).map(l => ({ ...l }));
  }

  subscribeLogs(sessionId: string, callback: (log: VolundrLog) => void): () => void {
    if (!this.logSubscribers.has(sessionId)) {
      this.logSubscribers.set(sessionId, new Set());
    }
    this.logSubscribers.get(sessionId)!.add(callback);

    return () => {
      this.logSubscribers.get(sessionId)?.delete(callback);
    };
  }

  async getCodeServerUrl(sessionId: string): Promise<string | null> {
    const session = this.sessions.find(s => s.id === sessionId);
    if (!session || session.status !== 'running') {
      return null;
    }
    if (session.origin === 'manual' && session.hostname) {
      return `https://${session.hostname}/`;
    }
    return `https://code.skuld.local/${sessionId}`;
  }

  async getChronicle(sessionId: string): Promise<SessionChronicle | null> {
    const chronicle = mockVolundrChronicles[sessionId];
    if (!chronicle) {
      return null;
    }
    return {
      events: chronicle.events.map(e => ({ ...e })),
      files: chronicle.files.map(f => ({ ...f })),
      commits: chronicle.commits.map(c => ({ ...c })),
      tokenBurn: [...chronicle.tokenBurn],
    };
  }

  subscribeChronicle(
    ...[
      ,/* sessionId */
      /* callback */
    ]: Parameters<IVolundrService['subscribeChronicle']>
  ): () => void {
    // Mock adapter does not simulate chronicle SSE events
    return () => {};
  }

  async getPullRequests(repoUrl: string, status = 'open'): Promise<PullRequest[]> {
    return this.pullRequests
      .filter(pr => pr.repoUrl === repoUrl && (status === 'all' || pr.status === status))
      .map(pr => ({ ...pr }));
  }

  async createPullRequest(
    sessionId: string,
    title?: string,
    targetBranch = 'main'
  ): Promise<PullRequest> {
    const session = this.sessions.find(s => s.id === sessionId);
    const sessionRepo = session?.source.type === 'git' ? session.source.repo : '';
    const repo = mockVolundrRepos.find(r => r.url.includes(sessionRepo));
    const prNumber = (parseInt(crypto.randomUUID().substring(0, 4), 16) % 200) + 100;

    const pr: PullRequest = {
      number: prNumber,
      title: title || `Session ${session?.name ?? sessionId} changes`,
      url: `${repo?.url ?? 'https://github.com/unknown'}/pull/${prNumber}`,
      repoUrl: repo?.url ?? '',
      provider: repo?.provider ?? 'github',
      sourceBranch: session?.source.type === 'git' ? session.source.branch : 'main',
      targetBranch,
      status: 'open',
      ciStatus: 'pending',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };

    this.pullRequests.push(pr);

    // Simulate CI transitioning to running then passed
    setTimeout(() => {
      const found = this.pullRequests.find(p => p.number === prNumber);
      if (found) {
        found.ciStatus = 'running';
      }
    }, 2000);

    setTimeout(() => {
      const found = this.pullRequests.find(p => p.number === prNumber);
      if (found) {
        found.ciStatus = 'passed';
      }
    }, 6000);

    return { ...pr };
  }

  async mergePullRequest(prNumber: number): Promise<MergeResult> {
    const pr = this.pullRequests.find(p => p.number === prNumber);
    if (pr) {
      pr.status = 'merged';
    }
    return { merged: true };
  }

  async getCIStatus(prNumber: number): Promise<CIStatusValue> {
    const pr = this.pullRequests.find(p => p.number === prNumber);
    return pr?.ciStatus ?? 'unknown';
  }

  async getSessionMcpServers(sessionId: string): Promise<McpServer[]> {
    const servers = mockVolundrMcpServers[sessionId];
    if (!servers) {
      return [];
    }
    return servers.map(s => ({ ...s }));
  }

  async searchTrackerIssues(query: string, _projectId?: string): Promise<TrackerIssue[]> {
    const lower = query.toLowerCase();
    return mockTrackerIssues
      .filter(
        issue =>
          issue.identifier.toLowerCase().includes(lower) ||
          issue.title.toLowerCase().includes(lower) ||
          (issue.labels ?? []).some(l => l.toLowerCase().includes(lower))
      )
      .map(issue => ({ ...issue }));
  }

  async getProjectRepoMappings(): Promise<ProjectRepoMapping[]> {
    return mockProjectRepoMappings.map(m => ({ ...m }));
  }

  async updateTrackerIssueStatus(
    issueId: string,
    status: TrackerIssue['status']
  ): Promise<TrackerIssue> {
    const issue = mockTrackerIssues.find(i => i.id === issueId);
    if (!issue) {
      throw new Error(`Tracker issue ${issueId} not found`);
    }
    issue.status = status;

    // Also update any sessions that have this issue linked
    for (const session of this.sessions) {
      if (session.trackerIssue?.id === issueId) {
        session.trackerIssue = { ...issue };
      }
    }
    this.notifySubscribers();

    return { ...issue };
  }

  async getIdentity(): Promise<VolundrIdentity> {
    return {
      userId: 'dev-user',
      email: 'dev@localhost',
      tenantId: 'default',
      roles: ['volundr:admin'],
      displayName: 'Dev User',
      status: 'active',
    };
  }

  async listUsers(): Promise<VolundrUser[]> {
    return [
      {
        id: 'dev-user',
        email: 'dev@localhost',
        displayName: 'Dev User',
        status: 'active',
      },
    ];
  }

  async getTenants(): Promise<VolundrTenant[]> {
    return [
      {
        id: 'default',
        path: 'default',
        name: 'Default',
        tier: 'developer',
        maxSessions: 100,
        maxStorageGb: 500,
      },
    ];
  }

  async getTenant(id: string): Promise<VolundrTenant | null> {
    if (id === 'default') {
      return {
        id: 'default',
        path: 'default',
        name: 'Default',
        tier: 'developer',
        maxSessions: 100,
        maxStorageGb: 500,
      };
    }
    return null;
  }

  async createTenant(data: {
    name: string;
    tier: string;
    maxSessions: number;
    maxStorageGb: number;
  }): Promise<VolundrTenant> {
    return {
      id: crypto.randomUUID(),
      path: data.name.toLowerCase().replace(/\s+/g, '-'),
      name: data.name,
      tier: data.tier,
      maxSessions: data.maxSessions,
      maxStorageGb: data.maxStorageGb,
      createdAt: new Date().toISOString(),
    };
  }

  async deleteTenant(_id: string): Promise<void> {
    // no-op in mock
  }

  async updateTenant(
    _id: string,
    _data: {
      tier?: string;
      maxSessions?: number;
      maxStorageGb?: number;
    }
  ): Promise<VolundrTenant> {
    return {
      id: _id,
      path: `tenants/${_id}`,
      name: 'Mock Tenant',
      tier: _data.tier ?? 'free',
      maxSessions: _data.maxSessions ?? 5,
      maxStorageGb: _data.maxStorageGb ?? 10,
    };
  }

  async getTenantMembers(_tenantId: string): Promise<VolundrMember[]> {
    return [
      { userId: 'user-1', tenantId: _tenantId, role: 'admin', grantedAt: '2025-01-01T00:00:00Z' },
      { userId: 'user-2', tenantId: _tenantId, role: 'member', grantedAt: '2025-01-02T00:00:00Z' },
    ];
  }

  async reprovisionUser(_userId: string): Promise<VolundrProvisioningResult> {
    return { success: true, userId: _userId, homePvc: `home-${_userId}`, errors: [] };
  }

  async reprovisionTenant(_tenantId: string): Promise<VolundrProvisioningResult[]> {
    return [
      { success: true, userId: 'user-1', homePvc: 'home-user-1', errors: [] },
      { success: true, userId: 'user-2', homePvc: 'home-user-2', errors: [] },
    ];
  }

  private userCredentials: VolundrCredential[] = [
    { name: 'github-token', keys: ['token'] },
    { name: 'openai-api-key', keys: ['api_key'] },
  ];

  private tenantCredentials: VolundrCredential[] = [
    { name: 'shared-docker-registry', keys: ['username', 'password', 'registry'] },
  ];

  async getUserCredentials(): Promise<VolundrCredential[]> {
    return this.userCredentials.map(c => ({ ...c, keys: [...c.keys] }));
  }

  async storeUserCredential(name: string, data: Record<string, string>): Promise<void> {
    const existing = this.userCredentials.findIndex(c => c.name === name);
    const credential: VolundrCredential = { name, keys: Object.keys(data) };
    if (existing !== -1) {
      this.userCredentials[existing] = credential;
    } else {
      this.userCredentials.push(credential);
    }
  }

  async deleteUserCredential(name: string): Promise<void> {
    this.userCredentials = this.userCredentials.filter(c => c.name !== name);
  }

  async getTenantCredentials(): Promise<VolundrCredential[]> {
    return this.tenantCredentials.map(c => ({ ...c, keys: [...c.keys] }));
  }

  async storeTenantCredential(name: string, data: Record<string, string>): Promise<void> {
    const existing = this.tenantCredentials.findIndex(c => c.name === name);
    const credential: VolundrCredential = { name, keys: Object.keys(data) };
    if (existing !== -1) {
      this.tenantCredentials[existing] = credential;
    } else {
      this.tenantCredentials.push(credential);
    }
  }

  async deleteTenantCredential(name: string): Promise<void> {
    this.tenantCredentials = this.tenantCredentials.filter(c => c.name !== name);
  }

  private mockCatalog: CatalogEntry[] = [
    {
      slug: 'linear',
      name: 'Linear',
      description: 'Issue tracker',
      integration_type: 'issue_tracker',
      adapter: 'volundr.adapters.outbound.linear.LinearAdapter',
      icon: 'linear',
      credential_schema: { required: ['api_key'], properties: { api_key: { type: 'string' } } },
      config_schema: {},
      mcp_server: {
        name: 'linear-mcp',
        command: 'npx',
        args: ['-y', '@anthropic-ai/linear-mcp-server'],
        env_from_credentials: { LINEAR_API_KEY: 'api_key' },
      },
      auth_type: 'api_key',
      oauth_scopes: [],
    },
    {
      slug: 'github',
      name: 'GitHub',
      description: 'GitHub source control',
      integration_type: 'source_control',
      adapter: 'volundr.adapters.outbound.github.GitHubProvider',
      icon: 'github',
      credential_schema: {
        required: ['personal_access_token'],
        properties: { personal_access_token: { type: 'string' } },
      },
      config_schema: {},
      mcp_server: {
        name: 'github-mcp',
        command: 'npx',
        args: ['-y', '@modelcontextprotocol/server-github'],
        env_from_credentials: { GITHUB_PERSONAL_ACCESS_TOKEN: 'personal_access_token' },
      },
      auth_type: 'api_key',
      oauth_scopes: [],
    },
    {
      slug: 'telegram',
      name: 'Telegram',
      description: 'Telegram messaging',
      integration_type: 'messaging',
      adapter: 'volundr.adapters.outbound.telegram.TelegramIntegration',
      icon: 'telegram',
      credential_schema: {
        required: ['bot_token', 'chat_id'],
        properties: { bot_token: { type: 'string' }, chat_id: { type: 'string' } },
      },
      config_schema: {},
      mcp_server: null,
      auth_type: 'api_key',
      oauth_scopes: [],
    },
  ];

  private mockIntegrations: IntegrationConnection[] = [
    {
      id: 'int-1',
      integrationType: 'issue_tracker',
      adapter: 'volundr.adapters.outbound.linear.LinearAdapter',
      credentialName: 'linear-api-key',
      config: {},
      slug: 'linear',
      enabled: true,
      createdAt: '2025-01-15T10:00:00Z',
      updatedAt: '2025-01-15T10:00:00Z',
    },
  ];

  async getIntegrationCatalog(): Promise<CatalogEntry[]> {
    return [...this.mockCatalog];
  }

  async getIntegrations(): Promise<IntegrationConnection[]> {
    return this.mockIntegrations.map(i => ({ ...i }));
  }

  async createIntegration(
    connection: Omit<IntegrationConnection, 'id' | 'createdAt' | 'updatedAt'>
  ): Promise<IntegrationConnection> {
    const now = new Date().toISOString();
    const created: IntegrationConnection = {
      ...connection,
      id: `int-${crypto.randomUUID().substring(0, 6)}`,
      createdAt: now,
      updatedAt: now,
    };
    this.mockIntegrations.push(created);
    return { ...created };
  }

  async deleteIntegration(id: string): Promise<void> {
    this.mockIntegrations = this.mockIntegrations.filter(i => i.id !== id);
  }

  async testIntegration(_id: string): Promise<IntegrationTestResult> {
    return {
      success: true,
      provider: 'linear',
      workspace: 'Mock Workspace',
      user: 'Dev User',
    };
  }

  private storedCredentials: import('@/models').StoredCredential[] = [
    {
      id: 'cred-1',
      name: 'github-token',
      secretType: 'api_key',
      keys: ['api_key'],
      metadata: {},
      createdAt: '2025-01-15T10:00:00Z',
      updatedAt: '2025-01-15T10:00:00Z',
    },
    {
      id: 'cred-2',
      name: 'deploy-key',
      secretType: 'ssh_key',
      keys: ['private_key', 'public_key'],
      metadata: {},
      createdAt: '2025-02-01T09:00:00Z',
      updatedAt: '2025-02-01T09:00:00Z',
    },
  ];

  async getCredentials(
    type?: import('@/models').SecretType
  ): Promise<import('@/models').StoredCredential[]> {
    if (type) {
      return this.storedCredentials.filter(c => c.secretType === type);
    }
    return [...this.storedCredentials];
  }

  async getCredential(name: string): Promise<import('@/models').StoredCredential | null> {
    return this.storedCredentials.find(c => c.name === name) ?? null;
  }

  async createCredential(
    req: import('@/models').CredentialCreateRequest
  ): Promise<import('@/models').StoredCredential> {
    const cred: import('@/models').StoredCredential = {
      id: `cred-${Date.now()}`,
      name: req.name,
      secretType: req.secretType,
      keys: Object.keys(req.data),
      metadata: req.metadata ?? {},
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    this.storedCredentials.push(cred);
    return cred;
  }

  async deleteCredential(name: string): Promise<void> {
    this.storedCredentials = this.storedCredentials.filter(c => c.name !== name);
  }

  async getCredentialTypes(): Promise<import('@/models').SecretTypeInfo[]> {
    return [
      {
        type: 'api_key',
        label: 'API Key',
        description: 'API keys for external services',
        fields: [{ key: 'api_key', label: 'API Key', type: 'password', required: true }],
        defaultMountType: 'env',
      },
      {
        type: 'oauth_token',
        label: 'OAuth Token',
        description: 'OAuth access and refresh tokens',
        fields: [
          { key: 'access_token', label: 'Access Token', type: 'password', required: true },
          { key: 'refresh_token', label: 'Refresh Token', type: 'password', required: false },
        ],
        defaultMountType: 'env',
      },
      {
        type: 'git_credential',
        label: 'Git Credential',
        description: 'Git authentication credentials',
        fields: [{ key: 'url', label: 'Credential URL', type: 'text', required: true }],
        defaultMountType: 'file',
      },
      {
        type: 'ssh_key',
        label: 'SSH Key',
        description: 'SSH private key for authentication',
        fields: [
          { key: 'private_key', label: 'Private Key', type: 'textarea', required: true },
          { key: 'public_key', label: 'Public Key', type: 'textarea', required: false },
        ],
        defaultMountType: 'file',
      },
      {
        type: 'tls_cert',
        label: 'TLS Certificate',
        description: 'TLS certificate and private key pair',
        fields: [
          { key: 'certificate', label: 'Certificate', type: 'textarea', required: true },
          { key: 'private_key', label: 'Private Key', type: 'textarea', required: true },
        ],
        defaultMountType: 'file',
      },
      {
        type: 'generic',
        label: 'Generic Secret',
        description: 'Custom key-value secret data',
        fields: [],
        defaultMountType: 'env',
      },
    ];
  }

  // ── Workspace management ──────────────────────────────────────────

  private mockWorkspaces: VolundrWorkspace[] = [
    {
      id: 'ws-1',
      pvcName: 'pvc-session-abc123',
      sessionId: 'session-1',
      ownerId: 'user-1',
      tenantId: 'tenant-1',
      sizeGb: 5,
      status: 'active',
      createdAt: new Date(Date.now() - 86400000 * 7).toISOString(),
    },
    {
      id: 'ws-2',
      pvcName: 'pvc-session-def456',
      sessionId: 'session-2',
      ownerId: 'user-1',
      tenantId: 'tenant-1',
      sizeGb: 3,
      status: 'archived',
      createdAt: new Date(Date.now() - 86400000 * 30).toISOString(),
      archivedAt: new Date(Date.now() - 86400000 * 14).toISOString(),
    },
  ];

  async listWorkspaces(status?: WorkspaceStatus): Promise<VolundrWorkspace[]> {
    await new Promise(r => setTimeout(r, 300));
    if (status) {
      return this.mockWorkspaces.filter(w => w.status === status);
    }
    return this.mockWorkspaces.filter(w => w.status !== 'deleted');
  }

  async listAllWorkspaces(status?: WorkspaceStatus): Promise<VolundrWorkspace[]> {
    await new Promise(r => setTimeout(r, 300));
    if (status) {
      return this.mockWorkspaces.filter(w => w.status === status);
    }
    return this.mockWorkspaces.filter(w => w.status !== 'deleted');
  }

  async restoreWorkspace(id: string): Promise<void> {
    await new Promise(r => setTimeout(r, 300));
    const ws = this.mockWorkspaces.find(w => w.id === id);
    if (ws) {
      ws.status = 'active';
      ws.archivedAt = undefined;
    }
  }

  async deleteWorkspace(id: string): Promise<void> {
    await new Promise(r => setTimeout(r, 300));
    const ws = this.mockWorkspaces.find(w => w.id === id);
    if (ws) {
      ws.status = 'deleted';
    }
  }

  async bulkDeleteWorkspaces(
    sessionIds: string[]
  ): Promise<{ deleted: number; failed: Array<{ session_id: string; error: string }> }> {
    await new Promise(r => setTimeout(r, 300));
    let deleted = 0;
    for (const sid of sessionIds) {
      const ws = this.mockWorkspaces.find(w => w.sessionId === sid);
      if (ws) {
        ws.status = 'deleted';
        deleted++;
      }
    }
    return { deleted, failed: [] };
  }

  async getAdminSettings(): Promise<AdminSettings> {
    return { storage: { homeEnabled: true, fileManagerEnabled: true } };
  }

  async updateAdminSettings(data: { storage?: AdminStorageSettings }): Promise<AdminSettings> {
    return {
      storage: {
        homeEnabled: data.storage?.homeEnabled ?? true,
        fileManagerEnabled: data.storage?.fileManagerEnabled ?? true,
      },
    };
  }

  async getFeatureModules(
    _scope?: import('@/models').FeatureScope
  ): Promise<import('@/models').FeatureModule[]> {
    if (_scope === 'session') {
      return [
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
    }
    return [];
  }

  async toggleFeature(_key: string, _enabled: boolean): Promise<import('@/models').FeatureModule> {
    return {
      key: _key,
      label: _key,
      icon: 'settings',
      scope: 'admin',
      enabled: _enabled,
      defaultEnabled: true,
      adminOnly: false,
      order: 0,
    };
  }

  async getUserFeaturePreferences(): Promise<import('@/models').UserFeaturePreference[]> {
    return [];
  }

  async updateUserFeaturePreferences(
    preferences: import('@/models').UserFeaturePreference[]
  ): Promise<import('@/models').UserFeaturePreference[]> {
    return preferences;
  }

  async listTokens(): Promise<import('@/modules/volundr/models').PersonalAccessToken[]> {
    return [];
  }

  async createToken(name: string): Promise<import('@/modules/volundr/models').CreatePATResult> {
    return {
      id: 'mock-pat-id',
      name,
      token: 'pat_mock_token_value',
      createdAt: new Date().toISOString(),
    };
  }

  async revokeToken(_id: string): Promise<void> {
    // no-op in mock
  }

  private notifySubscribers(): void {
    for (const callback of this.subscribers) {
      callback(this.sessions.map(s => ({ ...s })));
    }
    // Also notify stats subscribers when sessions change
    for (const callback of this.statsSubscribers) {
      callback({ ...this.stats });
    }
  }

  private notifyMessageSubscribers(sessionId: string, message: VolundrMessage): void {
    const subscribers = this.messageSubscribers.get(sessionId);
    if (subscribers) {
      for (const callback of subscribers) {
        callback({ ...message });
      }
    }
  }
}
