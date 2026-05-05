import { rewriteOrigin } from '@/utils';
import type { IVolundrService } from '@/modules/volundr/ports';
import type {
  SessionSource,
  VolundrSession,
  VolundrStats,
  VolundrModel,
  VolundrRepo,
  VolundrMessage,
  VolundrLog,
  SessionChronicle,
  ClusterResourceInfo,
  SessionStatus,
  RepoProvider,
  PullRequest,
  MergeResult,
  CIStatusValue,
  PRStatus,
  McpServerConfig,
  McpServerType,
  VolundrPreset,
  VolundrTemplate,
  CliTool,
  VolundrIdentity,
  VolundrUser,
  VolundrTenant,
  VolundrCredential,
  IntegrationConnection,
  IntegrationTestResult,
  CatalogEntry,
  StoredCredential,
  CredentialCreateRequest,
  SecretType,
  SecretTypeInfo,
  VolundrWorkspace,
  WorkspaceStatus,
  VolundrMember,
  VolundrProvisioningResult,
  AdminSettings,
  AdminStorageSettings,
  FeatureModule,
  FeatureScope,
  UserFeaturePreference,
} from '@/modules/volundr/models';
import { createApiClient, ApiClientError, getAccessToken } from './client';
import type {
  ApiSessionResponse,
  ApiSessionCreate,
  ApiSessionStatus,
  ApiModelInfo,
  ApiRepoInfo,
  ApiReposResponse,
  ApiStatsResponse,
  ApiMessageResponse,
  ApiMessageCreate,
  ApiLogResponse,
  ApiChronicleResponse,
  ApiTemplateResponse,
  ApiPresetResponse,
  ApiMcpServerConfig,
  ApiCreateSecretResponse,
  SSESessionPayload,
  SSESessionDeletedPayload,
  SSEStatsPayload,
  SSEMessagePayload,
  SSELogPayload,
  SSEChroniclePayload,
  ApiPullRequestResponse,
  ApiPRCreateRequest,
  ApiPRMergeRequest,
  ApiMergeResultResponse,
  ApiCIStatusResponse,
  ApiIdentityResponse,
  ApiUserResponse,
  ApiTenantResponse,
  ApiCredentialListResponse,
  ApiStoredCredentialResponse,
  ApiStoredCredentialListResponse,
  ApiSecretTypeInfoResponse,
  ApiWorkspaceResponse,
  ApiClusterResourceInfo,
  ApiFeatureModuleResponse,
  ApiUserFeaturePreferenceResponse,
  ApiSessionDefinitionResponse,
} from './volundr.types';

/**
 * API client for Volundr service
 */
const api = createApiClient('/api/v1/volundr');
const niuuApi = createApiClient('/api/v1/niuu');
const usersApi = createApiClient('/api/v1/users');

/**
 * SSE stream endpoint
 */
const SSE_ENDPOINT = '/api/v1/volundr/sessions/stream';

/**
 * Reconnection delay after SSE connection failure (starts at 1s, max 30s)
 */
const SSE_RECONNECT_BASE_MS = 1000;
const SSE_RECONNECT_MAX_MS = 30000;

/**
 * Default model metadata for when API doesn't provide it
 */
const DEFAULT_MODEL_METADATA: Record<string, Partial<VolundrModel>> = {
  // Claude models
  'claude-opus-4-20250514': { provider: 'cloud', tier: 'frontier', color: 'purple', cost: '$15/M' },
  'claude-sonnet-4-20250514': {
    provider: 'cloud',
    tier: 'balanced',
    color: 'purple',
    cost: '$3/M',
  },
  'claude-haiku-3-5-20241022': {
    provider: 'cloud',
    tier: 'execution',
    color: 'purple',
    cost: '$0.25/M',
  },
  // Local models
  'qwen2.5-coder:32b': { provider: 'local', tier: 'execution', color: 'cyan', vram: '24GB' },
  'deepseek-r1:70b': { provider: 'local', tier: 'reasoning', color: 'emerald', vram: '48GB' },
  'glm-4:9b': { provider: 'local', tier: 'execution', color: 'amber', vram: '8GB' },
};

/**
 * Map API tenant response to UI model
 */
function mapTenant(t: ApiTenantResponse): VolundrTenant {
  return {
    id: t.id,
    path: t.path,
    name: t.name,
    parentId: t.parent_id ?? undefined,
    tier: t.tier,
    maxSessions: t.max_sessions,
    maxStorageGb: t.max_storage_gb,
    createdAt: t.created_at ?? undefined,
  };
}

/**
 * Map API session status to UI session status
 */
function mapSessionStatus(apiStatus: ApiSessionStatus): SessionStatus {
  if (apiStatus === 'failed') {
    return 'error';
  }
  return apiStatus;
}

/**
 * Map a tracker issue status string to the TrackerIssueStatus union.
 */
function mapTrackerStatus(status: string): import('@/models').TrackerIssueStatus {
  const lower = status.toLowerCase().replace(/\s+/g, '_');
  const valid = ['backlog', 'todo', 'in_progress', 'done', 'cancelled'] as const;
  if ((valid as readonly string[]).includes(lower)) {
    return lower as import('@/models').TrackerIssueStatus;
  }
  return 'todo';
}

/**
 * Extract the host (hostname + port) from an endpoint URL.
 * Returns undefined when the URL is missing or unparseable.
 */
function extractHost(endpoint: string | null | undefined): string | undefined {
  if (!endpoint) {
    return undefined;
  }
  try {
    const rewritten = rewriteOrigin(endpoint);
    return new URL(rewritten).host;
  } catch {
    return undefined;
  }
}

/**
 * Transform API session response to UI model
 */
function transformSource(apiSource: ApiSessionResponse['source']): SessionSource {
  if (apiSource.type === 'local_mount') {
    return {
      type: 'local_mount',
      local_path: apiSource.local_path,
      paths: (apiSource.paths ?? []).map(p => ({
        host_path: p.host_path,
        mount_path: p.mount_path,
        read_only: p.read_only,
      })),
      node_selector: apiSource.node_selector,
    };
  }
  return {
    type: 'git',
    repo: apiSource.repo ?? '',
    branch: apiSource.branch ?? 'main',
  };
}

function transformSession(api: ApiSessionResponse): VolundrSession {
  return {
    id: api.id,
    name: api.name,
    model: api.model,
    status: mapSessionStatus(api.status),
    source: transformSource(api.source),
    lastActive: new Date(api.last_active).getTime(),
    messageCount: api.message_count,
    tokensUsed: api.tokens_used,
    podName: api.pod_name ?? undefined,
    error: api.error ?? undefined,
    hostname: extractHost(api.chat_endpoint) ?? extractHost(api.code_endpoint),
    chatEndpoint: api.chat_endpoint ? rewriteOrigin(api.chat_endpoint) : undefined,
    codeEndpoint: api.code_endpoint ? rewriteOrigin(api.code_endpoint) : undefined,
    taskType: api.task_type ?? undefined,
    activityState: api.activity_state ?? undefined,
    ownerId: api.owner_id ?? undefined,
    tenantId: api.tenant_id ?? undefined,
    trackerIssue: api.tracker_issue_id
      ? {
          id: api.tracker_issue_id,
          identifier: api.tracker_issue_id,
          title: '',
          status: 'todo',
          url: api.issue_tracker_url ?? '',
        }
      : undefined,
  };
}

/**
 * Transform API model info to UI model
 */
function transformModel(api: ApiModelInfo): VolundrModel {
  const defaults = DEFAULT_MODEL_METADATA[api.id] ?? {};

  return {
    name: api.name,
    provider: api.provider,
    tier: api.tier,
    color: api.color,
    cost: api.cost_per_million_tokens ? `$${api.cost_per_million_tokens}/M` : defaults.cost,
    vram: api.vram_required ?? defaults.vram,
  };
}

/**
 * Transform API repo info to UI model
 */
function transformRepo(apiRepo: ApiRepoInfo): VolundrRepo {
  return {
    provider: apiRepo.provider as RepoProvider,
    org: apiRepo.org,
    name: apiRepo.name,
    cloneUrl: apiRepo.clone_url ?? `${apiRepo.url}.git`,
    url: apiRepo.url,
    defaultBranch: apiRepo.default_branch,
    branches: apiRepo.branches ?? [],
  };
}

/**
 * Compute stats from sessions list (fallback when no stats endpoint)
 */
function computeStatsFromSessions(sessions: VolundrSession[]): VolundrStats {
  const activeSessions = sessions.filter(s => s.status === 'running').length;
  const totalSessions = sessions.length;
  const tokensToday = sessions.reduce((sum, s) => sum + s.tokensUsed, 0);

  return {
    activeSessions,
    totalSessions,
    tokensToday,
    localTokens: 0, // Cannot compute without model info per session
    cloudTokens: tokensToday, // Assume all cloud for now
    costToday: 0, // Cannot compute without pricing info
  };
}

/**
 * Transform API message response to UI model
 */
function transformMessage(apiMsg: ApiMessageResponse): VolundrMessage {
  return {
    id: apiMsg.id,
    sessionId: apiMsg.session_id,
    role: apiMsg.role,
    content: apiMsg.content,
    timestamp: new Date(apiMsg.created_at).getTime(),
    tokensIn: apiMsg.tokens_in ?? undefined,
    tokensOut: apiMsg.tokens_out ?? undefined,
    latency: apiMsg.latency_ms ?? undefined,
  };
}

/**
 * Transform API log response to UI model
 */
function transformLog(apiLog: ApiLogResponse): VolundrLog {
  return {
    id: apiLog.id,
    sessionId: apiLog.session_id,
    timestamp: new Date(apiLog.timestamp).getTime(),
    level: apiLog.level,
    source: apiLog.source,
    message: apiLog.message,
  };
}

/**
 * Transform API chronicle timeline response to UI model
 */
function transformChronicle(api: ApiChronicleResponse): SessionChronicle {
  return {
    events: api.events.map(e => ({
      t: e.t,
      type: e.type,
      label: e.label,
      ...(e.tokens != null && { tokens: e.tokens }),
      ...(e.action != null && { action: e.action }),
      ...(e.ins != null && { ins: e.ins }),
      ...(e.del != null && { del: e.del }),
      ...(e.hash != null && { hash: e.hash }),
      ...(e.exit != null && { exit: e.exit }),
    })),
    files: api.files.map(f => ({
      path: f.path,
      status: f.status,
      ins: f.ins,
      del: f.del,
    })),
    commits: api.commits.map(c => ({
      hash: c.hash,
      msg: c.msg,
      time: c.time,
    })),
    tokenBurn: api.token_burn,
  };
}

/**
 * Map API PR status string to typed PRStatus
 */
function mapPRStatus(status: string): PRStatus {
  if (status === 'open' || status === 'closed' || status === 'merged') {
    return status;
  }
  return 'open';
}

/**
 * Map API CI status string to typed CIStatusValue
 */
function mapCIStatus(status: string | null | undefined): CIStatusValue | undefined {
  if (!status) {
    return undefined;
  }
  const valid: CIStatusValue[] = ['pending', 'running', 'passed', 'failed', 'unknown'];
  if (valid.includes(status as CIStatusValue)) {
    return status as CIStatusValue;
  }
  return 'unknown';
}

/**
 * Transform API pull request response to UI model
 */
function transformPullRequest(apiPR: ApiPullRequestResponse): PullRequest {
  return {
    number: apiPR.number,
    title: apiPR.title,
    url: apiPR.url,
    repoUrl: apiPR.repo_url,
    provider: apiPR.provider,
    sourceBranch: apiPR.source_branch,
    targetBranch: apiPR.target_branch,
    status: mapPRStatus(apiPR.status),
    description: apiPR.description ?? undefined,
    ciStatus: mapCIStatus(apiPR.ci_status),
    reviewStatus: apiPR.review_status ?? undefined,
    createdAt: apiPR.created_at ?? undefined,
    updatedAt: apiPR.updated_at ?? undefined,
  };
}

/**
 * Transform API MCP server config to UI model
 */
function transformMcpServer(apiServer: ApiMcpServerConfig): McpServerConfig {
  return {
    name: apiServer.name,
    type: apiServer.type as McpServerType,
    command: apiServer.command,
    url: apiServer.url,
    args: apiServer.args,
  };
}

/**
 * Transform API template response to UI model
 */
function transformTemplate(apiTemplate: ApiTemplateResponse): VolundrTemplate {
  return {
    name: apiTemplate.name,
    description: apiTemplate.description,
    isDefault: apiTemplate.is_default,
    repos: apiTemplate.repos.map(r => ({ repo: (r as Record<string, string>).repo ?? '', ...r })),
    setupScripts: apiTemplate.setup_scripts,
    workspaceLayout: apiTemplate.workspace_layout,
    cliTool: (apiTemplate.cli_tool as CliTool) ?? 'claude',
    workloadType: apiTemplate.workload_type ?? 'development',
    model: apiTemplate.model,
    systemPrompt: apiTemplate.system_prompt,
    resourceConfig: apiTemplate.resource_config ?? {},
    mcpServers: (apiTemplate.mcp_servers ?? []).map(transformMcpServer),
    envVars: apiTemplate.env_vars ?? {},
    envSecretRefs: apiTemplate.env_secret_refs ?? [],
    workloadConfig: apiTemplate.workload_config ?? {},
    terminalSidecar: apiTemplate.terminal_sidecar
      ? {
          enabled: apiTemplate.terminal_sidecar.enabled,
          allowedCommands: apiTemplate.terminal_sidecar.allowed_commands ?? [],
        }
      : { enabled: false, allowedCommands: [] },
    skills: apiTemplate.skills ?? [],
    rules: apiTemplate.rules ?? [],
  };
}

/**
 * Transform API preset response to UI model
 */
function transformPreset(apiPreset: ApiPresetResponse): VolundrPreset {
  return {
    id: apiPreset.id,
    name: apiPreset.name,
    description: apiPreset.description,
    isDefault: apiPreset.is_default,
    createdAt: apiPreset.created_at,
    updatedAt: apiPreset.updated_at,
    cliTool: (apiPreset.cli_tool as CliTool) ?? 'claude',
    workloadType: apiPreset.workload_type ?? 'development',
    model: apiPreset.model,
    systemPrompt: apiPreset.system_prompt,
    resourceConfig: apiPreset.resource_config ?? {},
    mcpServers: (apiPreset.mcp_servers ?? []).map(transformMcpServer),
    terminalSidecar: apiPreset.terminal_sidecar
      ? {
          enabled: apiPreset.terminal_sidecar.enabled,
          allowedCommands: apiPreset.terminal_sidecar.allowed_commands ?? [],
        }
      : { enabled: false, allowedCommands: [] },
    skills: apiPreset.skills ?? [],
    rules: apiPreset.rules ?? [],
    envVars: apiPreset.env_vars ?? {},
    envSecretRefs: apiPreset.env_secret_refs ?? [],
    source: apiPreset.source ? transformSource(apiPreset.source) : null,
    integrationIds: apiPreset.integration_ids ?? [],
    setupScripts: apiPreset.setup_scripts ?? [],
    workloadConfig: apiPreset.workload_config ?? {},
  };
}

/**
 * Transform SSE session payload to UI model
 */
function transformSSESession(payload: SSESessionPayload): VolundrSession {
  const source: import('@/models').SessionSource = payload.source
    ? transformSource(payload.source)
    : { type: 'git', repo: payload.repo ?? '', branch: payload.branch ?? 'main' };

  return {
    id: payload.id,
    name: payload.name,
    model: payload.model,
    status: mapSessionStatus(payload.status),
    source,
    lastActive: new Date(payload.last_active).getTime(),
    messageCount: payload.message_count,
    tokensUsed: payload.tokens_used,
    podName: payload.pod_name ?? undefined,
    error: payload.error ?? undefined,
    hostname: extractHost(payload.chat_endpoint) ?? extractHost(payload.code_endpoint),
    chatEndpoint: payload.chat_endpoint ? rewriteOrigin(payload.chat_endpoint) : undefined,
    codeEndpoint: payload.code_endpoint ? rewriteOrigin(payload.code_endpoint) : undefined,
    taskType: payload.task_type ?? undefined,
    activityState: payload.activity_state ?? undefined,
    ownerId: payload.owner_id ?? undefined,
    tenantId: payload.tenant_id ?? undefined,
    trackerIssue: payload.tracker_issue_id
      ? {
          id: payload.tracker_issue_id,
          identifier: payload.tracker_issue_id,
          title: '',
          status: 'todo',
          url: payload.issue_tracker_url ?? '',
        }
      : undefined,
  };
}

/**
 * API implementation of IVolundrService
 *
 * Connects to the Volundr backend API with real-time updates via SSE.
 */
export class ApiVolundrService implements IVolundrService {
  private subscribers = new Set<(sessions: VolundrSession[]) => void>();
  private statsSubscribers = new Set<(stats: VolundrStats) => void>();
  private messageSubscribers = new Map<string, Set<(message: VolundrMessage) => void>>();
  private logSubscribers = new Map<string, Set<(log: VolundrLog) => void>>();
  private chronicleSubscribers = new Map<string, Set<(chronicle: SessionChronicle) => void>>();
  private sseAbort: AbortController | null = null;
  private reconnectAttempts = 0;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private cachedSessions: VolundrSession[] = [];
  private cachedStats: VolundrStats | null = null;

  async getSessionDefinitions(): Promise<import('@/modules/volundr/models').SessionDefinition[]> {
    try {
      const response = await api.get<ApiSessionDefinitionResponse[]>('/session-definitions');
      return response.map(d => ({
        key: d.key,
        displayName: d.display_name,
        description: d.description,
        labels: d.labels,
        defaultModel: d.default_model,
      }));
    } catch {
      return [];
    }
  }

  async getSessions(): Promise<VolundrSession[]> {
    const response = await api.get<ApiSessionResponse[]>('/sessions');
    this.cachedSessions = response.map(transformSession);
    return this.cachedSessions;
  }

  async getSession(id: string): Promise<VolundrSession | null> {
    try {
      const response = await api.get<ApiSessionResponse>(`/sessions/${id}`);
      return transformSession(response);
    } catch (error) {
      if (error instanceof ApiClientError && error.status === 404) {
        return null;
      }
      throw error;
    }
  }

  async getActiveSessions(): Promise<VolundrSession[]> {
    const sessions = await this.getSessions();
    return sessions.filter(s => s.status === 'running');
  }

  async getStats(): Promise<VolundrStats> {
    try {
      // Try the stats endpoint first
      const response = await api.get<ApiStatsResponse>('/stats');
      return {
        activeSessions: response.active_sessions,
        totalSessions: response.total_sessions,
        tokensToday: response.tokens_today,
        localTokens: response.local_tokens,
        cloudTokens: response.cloud_tokens,
        costToday: response.cost_today,
      };
    } catch (error) {
      // Fall back to computing from sessions
      if (error instanceof ApiClientError && error.status === 404) {
        const sessions = await this.getSessions();
        return computeStatsFromSessions(sessions);
      }
      throw error;
    }
  }

  async getFeatures(): Promise<import('@/models').VolundrFeatures> {
    try {
      const response = await api.get<{
        local_mounts_enabled: boolean;
        file_manager_enabled: boolean;
        mini_mode: boolean;
      }>('/feature-flags');
      return {
        localMountsEnabled: response.local_mounts_enabled,
        fileManagerEnabled: response.file_manager_enabled ?? true,
        miniMode: response.mini_mode ?? false,
      };
    } catch {
      return { localMountsEnabled: false, fileManagerEnabled: true, miniMode: false };
    }
  }

  async getModels(): Promise<Record<string, VolundrModel>> {
    const response = await api.get<ApiModelInfo[]>('/models');
    const models: Record<string, VolundrModel> = {};

    for (const apiModel of response) {
      models[apiModel.id] = transformModel(apiModel);
    }

    return models;
  }

  async getRepos(): Promise<VolundrRepo[]> {
    const response = await niuuApi.get<ApiReposResponse>('/repos');
    return Object.values(response).flat().map(transformRepo);
  }

  subscribe(callback: (sessions: VolundrSession[]) => void): () => void {
    this.subscribers.add(callback);

    // Start SSE connection if this is the first subscriber
    if (this.subscribers.size === 1 && this.statsSubscribers.size === 0) {
      this.connectSSE();
    }

    // Immediately notify with cached data if available
    if (this.cachedSessions.length > 0) {
      callback([...this.cachedSessions]);
    }

    // Return unsubscribe function
    return () => {
      this.subscribers.delete(callback);

      // Disconnect SSE if no more subscribers
      if (this.subscribers.size === 0 && this.statsSubscribers.size === 0) {
        this.disconnectSSE();
      }
    };
  }

  /**
   * Subscribe to stats updates via SSE
   */
  subscribeStats(callback: (stats: VolundrStats) => void): () => void {
    this.statsSubscribers.add(callback);

    // Start SSE connection if this is the first subscriber
    if (this.subscribers.size === 0 && this.statsSubscribers.size === 1) {
      this.connectSSE();
    }

    // Immediately notify with cached data if available
    if (this.cachedStats) {
      callback({ ...this.cachedStats });
    }

    // Return unsubscribe function
    return () => {
      this.statsSubscribers.delete(callback);

      // Disconnect SSE if no more subscribers
      if (this.subscribers.size === 0 && this.statsSubscribers.size === 0) {
        this.disconnectSSE();
      }
    };
  }

  async getTemplates(): Promise<VolundrTemplate[]> {
    const response = await api.get<ApiTemplateResponse[]>('/templates');
    return response.map(transformTemplate);
  }

  async getTemplate(name: string): Promise<VolundrTemplate | null> {
    try {
      const response = await api.get<ApiTemplateResponse>(`/templates/${encodeURIComponent(name)}`);
      return transformTemplate(response);
    } catch (error) {
      if (error instanceof ApiClientError && error.status === 404) {
        return null;
      }
      throw error;
    }
  }

  async saveTemplate(template: VolundrTemplate): Promise<VolundrTemplate> {
    const response = await api.post<ApiTemplateResponse>('/templates', template);
    return transformTemplate(response);
  }

  async getPresets(): Promise<VolundrPreset[]> {
    const response = await api.get<ApiPresetResponse[]>('/presets');
    return response.map(transformPreset);
  }

  async getPreset(id: string): Promise<VolundrPreset | null> {
    try {
      const response = await api.get<ApiPresetResponse>(`/presets/${id}`);
      return transformPreset(response);
    } catch (error) {
      if (error instanceof ApiClientError && error.status === 404) {
        return null;
      }
      throw error;
    }
  }

  async savePreset(
    preset: Omit<VolundrPreset, 'id' | 'createdAt' | 'updatedAt'> & { id?: string }
  ): Promise<VolundrPreset> {
    const body = {
      name: preset.name,
      description: preset.description,
      is_default: preset.isDefault,
      cli_tool: preset.cliTool,
      workload_type: preset.workloadType,
      model: preset.model,
      system_prompt: preset.systemPrompt,
      resource_config: preset.resourceConfig,
      mcp_servers: preset.mcpServers,
      terminal_sidecar: {
        enabled: preset.terminalSidecar.enabled,
        allowed_commands: preset.terminalSidecar.allowedCommands,
      },
      skills: preset.skills,
      rules: preset.rules,
      env_vars: preset.envVars,
      env_secret_refs: preset.envSecretRefs,
      source: preset.source
        ? preset.source.type === 'git'
          ? { type: 'git' as const, repo: preset.source.repo, branch: preset.source.branch }
          : {
              type: 'local_mount' as const,
              paths: preset.source.paths,
              node_selector: preset.source.node_selector,
            }
        : null,
      integration_ids: preset.integrationIds,
      setup_scripts: preset.setupScripts,
      workload_config: preset.workloadConfig,
    };

    if (preset.id) {
      const response = await api.put<ApiPresetResponse>(`/presets/${preset.id}`, body);
      return transformPreset(response);
    }
    const response = await api.post<ApiPresetResponse>('/presets', body);
    return transformPreset(response);
  }

  async deletePreset(id: string): Promise<void> {
    await api.delete(`/presets/${id}`);
  }

  async getAvailableMcpServers(): Promise<McpServerConfig[]> {
    const response = await api.get<ApiMcpServerConfig[]>('/mcp-servers');
    return response.map(transformMcpServer);
  }

  async getAvailableSecrets(): Promise<string[]> {
    return api.get<string[]>('/secrets');
  }

  async createSecret(
    name: string,
    data: Record<string, string>
  ): Promise<{ name: string; keys: string[] }> {
    return api.post<ApiCreateSecretResponse>('/secrets', { name, data });
  }

  async getClusterResources(): Promise<ClusterResourceInfo> {
    const response = await api.get<ApiClusterResourceInfo>('/resources');
    return {
      resourceTypes: response.resource_types.map(rt => ({
        name: rt.name,
        resourceKey: rt.resource_key,
        displayName: rt.display_name,
        unit: rt.unit,
        category: rt.category,
      })),
      nodes: response.nodes.map(n => ({
        name: n.name,
        labels: n.labels,
        allocatable: n.allocatable,
        allocated: n.allocated,
        available: n.available,
      })),
    };
  }

  async startSession(config: {
    name: string;
    source: import('@/models').SessionSource;
    model: string;
    templateName?: string;
    taskType?: string;
    definition?: string;
    terminalRestricted?: boolean;
    workspaceId?: string;
    credentialNames?: string[];
    integrationIds?: string[];
    resourceConfig?: Record<string, string | undefined>;
    trackerIssue?: import('@/models').TrackerIssue;
    systemPrompt?: string;
    initialPrompt?: string;
  }): Promise<VolundrSession> {
    const createRequest: ApiSessionCreate = {
      name: config.name,
      model: config.model,
      source: config.source,
      template_name: config.templateName ?? null,
      task_type: config.taskType ?? null,
      definition: config.definition ?? null,
      terminal_restricted: config.terminalRestricted ?? false,
      workspace_id: config.workspaceId ?? null,
      credential_names: config.credentialNames?.length ? config.credentialNames : undefined,
      integration_ids: config.integrationIds?.length ? config.integrationIds : undefined,
      resource_config: config.resourceConfig,
      system_prompt: config.systemPrompt,
      initial_prompt: config.initialPrompt,
      issue_id: config.trackerIssue?.identifier ?? null,
      issue_url: config.trackerIssue?.url ?? null,
    };

    const response = await api.post<ApiSessionResponse>('/sessions', createRequest);
    const session = transformSession(response);

    // The backend's POST /sessions already starts the session, so no
    // separate POST /start call is needed.

    // Update cached sessions and notify subscribers.
    // The SSE stream may have already delivered a session_created event for
    // this session, so deduplicate to avoid showing it twice in the UI.
    const existingIndex = this.cachedSessions.findIndex(s => s.id === session.id);
    if (existingIndex === -1) {
      this.cachedSessions = [session, ...this.cachedSessions];
    } else {
      this.cachedSessions[existingIndex] = session;
    }
    this.notifySessionSubscribers();

    return session;
  }

  async connectSession(config: { name: string; hostname: string }): Promise<VolundrSession> {
    // Manual sessions are local-only — no backend API call.
    // Start as 'starting' so the client-side probe verifies connectivity
    // before the UI shows the chat/terminal interface.
    const session: VolundrSession = {
      id: `manual-${Math.random().toString(36).substring(2, 10)}`,
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

    this.cachedSessions = [session, ...this.cachedSessions];
    this.notifySessionSubscribers();

    return session;
  }

  async updateSession(
    sessionId: string,
    updates: { name?: string; model?: string; branch?: string; tracker_issue_id?: string }
  ): Promise<VolundrSession> {
    const resp = await api.put<ApiSessionResponse>(`/sessions/${sessionId}`, updates);
    const session = transformSession(resp);
    this.cachedSessions = this.cachedSessions.map(s => (s.id === sessionId ? session : s));
    this.notifySessionSubscribers();
    return session;
  }

  async stopSession(sessionId: string): Promise<void> {
    const session = this.cachedSessions.find(s => s.id === sessionId);

    // Manual sessions only update local state
    if (session?.origin === 'manual') {
      session.status = 'stopped';
      this.notifySessionSubscribers();
      return;
    }

    await api.post(`/sessions/${sessionId}/stop`);

    if (session) {
      session.status = 'stopped';
      this.notifySessionSubscribers();
    }
  }

  async resumeSession(sessionId: string): Promise<void> {
    const session = this.cachedSessions.find(s => s.id === sessionId);

    // Manual sessions only update local state — set to 'starting' so the
    // client-side probe re-verifies WebSocket connectivity before showing UI.
    if (session?.origin === 'manual') {
      session.status = 'starting';
      session.lastActive = Date.now();
      this.notifySessionSubscribers();
      return;
    }

    await api.post(`/sessions/${sessionId}/start`);

    if (session) {
      session.status = 'starting';
      this.notifySessionSubscribers();
    }
  }

  async deleteSession(sessionId: string, cleanup: string[] = []): Promise<void> {
    const session = this.cachedSessions.find(s => s.id === sessionId);

    // Manual sessions: no-op on backend, just remove locally
    if (session?.origin !== 'manual') {
      const body = cleanup.length > 0 ? { cleanup } : undefined;
      await api.delete(`/sessions/${sessionId}`, body);
    }

    this.cachedSessions = this.cachedSessions.filter(s => s.id !== sessionId);
    this.notifySessionSubscribers();
  }

  async archiveSession(sessionId: string): Promise<void> {
    await api.post(`/sessions/${sessionId}/archive`);
    this.cachedSessions = this.cachedSessions.filter(s => s.id !== sessionId);
    this.notifySessionSubscribers();
  }

  async restoreSession(sessionId: string): Promise<void> {
    await api.post(`/sessions/${sessionId}/restore`);
  }

  async listArchivedSessions(): Promise<VolundrSession[]> {
    const response = await api.get<ApiSessionResponse[]>('/sessions?status=archived');
    return response.map(transformSession);
  }

  async getMessages(sessionId: string): Promise<VolundrMessage[]> {
    const response = await api.get<ApiMessageResponse[]>(`/sessions/${sessionId}/messages`);
    return response.map(transformMessage);
  }

  async sendMessage(sessionId: string, content: string): Promise<VolundrMessage> {
    const request: ApiMessageCreate = { content };
    const response = await api.post<ApiMessageResponse>(`/sessions/${sessionId}/messages`, request);
    return transformMessage(response);
  }

  subscribeMessages(sessionId: string, callback: (message: VolundrMessage) => void): () => void {
    if (!this.messageSubscribers.has(sessionId)) {
      this.messageSubscribers.set(sessionId, new Set());
    }
    this.messageSubscribers.get(sessionId)!.add(callback);

    // Ensure SSE is connected
    if (this.subscribers.size === 0 && this.statsSubscribers.size === 0 && !this.sseAbort) {
      this.connectSSE();
    }

    return () => {
      this.messageSubscribers.get(sessionId)?.delete(callback);
      if (this.messageSubscribers.get(sessionId)?.size === 0) {
        this.messageSubscribers.delete(sessionId);
      }
    };
  }

  async getLogs(sessionId: string, limit = 100): Promise<VolundrLog[]> {
    const response = await api.get<ApiLogResponse[]>(`/sessions/${sessionId}/logs?limit=${limit}`);
    return response.map(transformLog);
  }

  subscribeLogs(sessionId: string, callback: (log: VolundrLog) => void): () => void {
    if (!this.logSubscribers.has(sessionId)) {
      this.logSubscribers.set(sessionId, new Set());
    }
    this.logSubscribers.get(sessionId)!.add(callback);

    // Ensure SSE is connected
    if (this.subscribers.size === 0 && this.statsSubscribers.size === 0 && !this.sseAbort) {
      this.connectSSE();
    }

    return () => {
      this.logSubscribers.get(sessionId)?.delete(callback);
      if (this.logSubscribers.get(sessionId)?.size === 0) {
        this.logSubscribers.delete(sessionId);
      }
    };
  }

  async getCodeServerUrl(sessionId: string): Promise<string | null> {
    // Manual sessions: derive URL from hostname.
    // Check the cache first, then fall back to the ID prefix for cases
    // where the cache hasn't been populated yet (e.g. fresh popout window).
    const cached = this.cachedSessions.find(s => s.id === sessionId);
    if (cached?.origin === 'manual' || sessionId.startsWith('manual-')) {
      if (!cached || cached.status !== 'running' || !cached.hostname) {
        return null;
      }
      return `https://${cached.hostname}/`;
    }

    try {
      const response = await api.get<ApiSessionResponse>(`/sessions/${sessionId}`);
      if (response.status !== 'running') {
        return null;
      }
      return response.code_endpoint ? rewriteOrigin(response.code_endpoint) : null;
    } catch (error) {
      if (error instanceof ApiClientError && (error.status === 404 || error.status === 422)) {
        return null;
      }
      throw error;
    }
  }

  async getChronicle(sessionId: string): Promise<SessionChronicle | null> {
    try {
      const response = await api.get<ApiChronicleResponse>(
        `/chronicles/${encodeURIComponent(sessionId)}/timeline`
      );
      return transformChronicle(response);
    } catch (error) {
      if (error instanceof ApiClientError && error.status === 404) {
        return null;
      }
      throw error;
    }
  }

  subscribeChronicle(
    sessionId: string,
    callback: (chronicle: SessionChronicle) => void
  ): () => void {
    if (!this.chronicleSubscribers.has(sessionId)) {
      this.chronicleSubscribers.set(sessionId, new Set());
    }
    this.chronicleSubscribers.get(sessionId)!.add(callback);

    // Ensure SSE is connected
    if (this.subscribers.size === 0 && this.statsSubscribers.size === 0 && !this.sseAbort) {
      this.connectSSE();
    }

    return () => {
      this.chronicleSubscribers.get(sessionId)?.delete(callback);
      if (this.chronicleSubscribers.get(sessionId)?.size === 0) {
        this.chronicleSubscribers.delete(sessionId);
      }
    };
  }

  async getPullRequests(repoUrl: string, status = 'open'): Promise<PullRequest[]> {
    const params = new URLSearchParams({ repo_url: repoUrl, status });
    const response = await api.get<ApiPullRequestResponse[]>(`/repos/prs?${params}`);
    return response.map(transformPullRequest);
  }

  async createPullRequest(
    sessionId: string,
    title?: string,
    targetBranch = 'main'
  ): Promise<PullRequest> {
    const body: ApiPRCreateRequest = {
      session_id: sessionId,
      ...(title && { title }),
      target_branch: targetBranch,
    };
    const response = await api.post<ApiPullRequestResponse>('/repos/prs', body);
    return transformPullRequest(response);
  }

  async mergePullRequest(
    prNumber: number,
    repoUrl: string,
    mergeMethod = 'squash'
  ): Promise<MergeResult> {
    const params = new URLSearchParams({ repo_url: repoUrl });
    const body: ApiPRMergeRequest = { merge_method: mergeMethod };
    const response = await api.post<ApiMergeResultResponse>(
      `/repos/prs/${prNumber}/merge?${params}`,
      body
    );
    return { merged: response.merged };
  }

  async getCIStatus(prNumber: number, repoUrl: string, branch: string): Promise<CIStatusValue> {
    const params = new URLSearchParams({ repo_url: repoUrl, branch });
    const response = await api.get<ApiCIStatusResponse>(`/repos/prs/${prNumber}/ci?${params}`);
    return mapCIStatus(response.status) ?? 'unknown';
  }

  async getSessionMcpServers(_sessionId: string): Promise<import('@/models').McpServer[]> {
    // TODO: Implement when backend endpoint is available
    return [];
  }

  async searchTrackerIssues(
    query: string,
    _projectId?: string
  ): Promise<import('@/models').TrackerIssue[]> {
    interface TrackerIssue {
      id: string;
      identifier: string;
      title: string;
      status: string;
      assignee?: string;
      labels?: string[];
      priority?: number;
      url: string;
    }
    try {
      const results = await api.get<TrackerIssue[]>(
        `/issues/search?q=${encodeURIComponent(query)}`
      );
      return results.map(issue => ({
        id: issue.id,
        identifier: issue.identifier,
        title: issue.title,
        status: mapTrackerStatus(issue.status),
        assignee: issue.assignee ?? undefined,
        labels: issue.labels ?? [],
        priority: issue.priority ?? 0,
        url: issue.url,
      }));
    } catch {
      return [];
    }
  }

  async getProjectRepoMappings(): Promise<import('@/models').ProjectRepoMapping[]> {
    // TODO: Implement when backend endpoint is available
    return [];
  }

  async updateTrackerIssueStatus(
    issueId: string,
    issueStatus: import('@/models').TrackerIssueStatus
  ): Promise<import('@/models').TrackerIssue> {
    interface TrackerIssue {
      id: string;
      identifier: string;
      title: string;
      status: string;
      assignee?: string;
      labels?: string[];
      priority?: number;
      url: string;
    }
    const issue = await api.post<TrackerIssue>(`/issues/${issueId}/status`, {
      status: issueStatus,
    });
    return {
      id: issue.id,
      identifier: issue.identifier,
      title: issue.title,
      status: mapTrackerStatus(issue.status),
      assignee: issue.assignee ?? undefined,
      labels: issue.labels ?? [],
      priority: issue.priority ?? 0,
      url: issue.url,
    };
  }

  async getIdentity(): Promise<VolundrIdentity> {
    const response = await api.get<ApiIdentityResponse>('/me');
    return {
      userId: response.user_id,
      email: response.email,
      tenantId: response.tenant_id,
      roles: response.roles,
      displayName: response.display_name,
      status: response.status,
    };
  }

  async listUsers(): Promise<VolundrUser[]> {
    const response = await api.get<ApiUserResponse[]>('/users');
    return response.map(u => ({
      id: u.id,
      email: u.email,
      displayName: u.display_name,
      status: u.status,
      createdAt: u.created_at ?? undefined,
    }));
  }

  async getTenants(): Promise<VolundrTenant[]> {
    const response = await api.get<ApiTenantResponse[]>('/tenants');
    return response.map(mapTenant);
  }

  async getTenant(id: string): Promise<VolundrTenant | null> {
    try {
      const response = await api.get<ApiTenantResponse>(`/tenants/${id}`);
      return mapTenant(response);
    } catch (error) {
      if (error instanceof ApiClientError && error.status === 404) {
        return null;
      }
      throw error;
    }
  }

  async createTenant(data: {
    name: string;
    tier: string;
    maxSessions: number;
    maxStorageGb: number;
  }): Promise<VolundrTenant> {
    const response = await api.post<ApiTenantResponse>('/tenants', {
      name: data.name,
      tier: data.tier,
      max_sessions: data.maxSessions,
      max_storage_gb: data.maxStorageGb,
    });
    return mapTenant(response);
  }

  async deleteTenant(id: string): Promise<void> {
    await api.delete(`/tenants/${id}`);
  }

  async updateTenant(
    id: string,
    data: {
      tier?: string;
      maxSessions?: number;
      maxStorageGb?: number;
    }
  ): Promise<VolundrTenant> {
    const response = await api.put<ApiTenantResponse>(`/tenants/${id}`, {
      tier: data.tier,
      max_sessions: data.maxSessions,
      max_storage_gb: data.maxStorageGb,
    });
    return mapTenant(response);
  }

  async getTenantMembers(tenantId: string): Promise<VolundrMember[]> {
    const response = await api.get<
      Array<{
        user_id: string;
        tenant_id: string;
        role: string;
        granted_at: string | null;
      }>
    >(`/tenants/${tenantId}/members`);
    return response.map(m => ({
      userId: m.user_id,
      tenantId: m.tenant_id,
      role: m.role,
      grantedAt: m.granted_at ?? undefined,
    }));
  }

  async reprovisionUser(userId: string): Promise<VolundrProvisioningResult> {
    const response = await api.post<{
      success: boolean;
      user_id: string;
      home_pvc?: string;
      errors: string[];
    }>(`/users/${userId}/reprovision`, {});
    return {
      success: response.success,
      userId: response.user_id,
      homePvc: response.home_pvc,
      errors: response.errors,
    };
  }

  async reprovisionTenant(tenantId: string): Promise<VolundrProvisioningResult[]> {
    const response = await api.post<
      Array<{
        success: boolean;
        user_id: string;
        home_pvc?: string;
        errors: string[];
      }>
    >(`/tenants/${tenantId}/reprovision`, {});
    return response.map(r => ({
      success: r.success,
      userId: r.user_id,
      homePvc: r.home_pvc,
      errors: r.errors,
    }));
  }

  async getUserCredentials(): Promise<VolundrCredential[]> {
    const response = await api.get<ApiCredentialListResponse>('/secrets/user');
    return response.credentials.map(c => ({ name: c.name, keys: c.keys }));
  }

  async storeUserCredential(name: string, data: Record<string, string>): Promise<void> {
    await api.post('/secrets/user', { name, data });
  }

  async deleteUserCredential(name: string): Promise<void> {
    await api.delete(`/secrets/user/${encodeURIComponent(name)}`);
  }

  async getTenantCredentials(): Promise<VolundrCredential[]> {
    const response = await api.get<ApiCredentialListResponse>('/secrets/tenant');
    return response.credentials.map(c => ({ name: c.name, keys: c.keys }));
  }

  async storeTenantCredential(name: string, data: Record<string, string>): Promise<void> {
    await api.post('/secrets/tenant', { name, data });
  }

  async deleteTenantCredential(name: string): Promise<void> {
    await api.delete(`/secrets/tenant/${encodeURIComponent(name)}`);
  }

  async getIntegrationCatalog(): Promise<CatalogEntry[]> {
    return api.get<CatalogEntry[]>('/integrations/catalog');
  }

  async getIntegrations(): Promise<IntegrationConnection[]> {
    const response = await api.get<
      Array<{
        id: string;
        integration_type: string;
        adapter: string;
        credential_name: string;
        config: Record<string, string>;
        enabled: boolean;
        created_at: string;
        updated_at: string;
        slug: string;
      }>
    >('/integrations');
    return response.map(c => ({
      id: c.id,
      integrationType: c.integration_type as IntegrationConnection['integrationType'],
      adapter: c.adapter,
      credentialName: c.credential_name,
      config: c.config,
      enabled: c.enabled,
      createdAt: c.created_at,
      updatedAt: c.updated_at,
      slug: c.slug || '',
    }));
  }

  async getCredentials(type?: SecretType): Promise<StoredCredential[]> {
    const params = type ? `?secret_type=${type}` : '';
    const response = await api.get<ApiStoredCredentialListResponse>(`/credentials${params}`);
    return response.credentials.map(c => ({
      id: c.id,
      name: c.name,
      secretType: c.secret_type as SecretType,
      keys: c.keys,
      metadata: c.metadata,
      createdAt: c.created_at,
      updatedAt: c.updated_at,
    }));
  }

  async createIntegration(
    connection: Omit<IntegrationConnection, 'id' | 'createdAt' | 'updatedAt'>
  ): Promise<IntegrationConnection> {
    const body = {
      integration_type: connection.integrationType,
      adapter: connection.adapter,
      credential_name: connection.credentialName,
      config: connection.config,
      enabled: connection.enabled,
      slug: connection.slug || '',
    };
    const response = await api.post<{
      id: string;
      integration_type: string;
      adapter: string;
      credential_name: string;
      config: Record<string, string>;
      enabled: boolean;
      created_at: string;
      updated_at: string;
      slug: string;
    }>('/integrations', body);
    return {
      id: response.id,
      integrationType: response.integration_type as IntegrationConnection['integrationType'],
      adapter: response.adapter,
      credentialName: response.credential_name,
      config: response.config,
      enabled: response.enabled,
      createdAt: response.created_at,
      updatedAt: response.updated_at,
      slug: response.slug || '',
    };
  }

  async getCredential(name: string): Promise<StoredCredential | null> {
    try {
      const response = await api.get<ApiStoredCredentialResponse>(
        `/credentials/${encodeURIComponent(name)}`
      );
      return {
        id: response.id,
        name: response.name,
        secretType: response.secret_type as SecretType,
        keys: response.keys,
        metadata: response.metadata,
        createdAt: response.created_at,
        updatedAt: response.updated_at,
      };
    } catch (error) {
      if (error instanceof ApiClientError && error.status === 404) {
        return null;
      }
      throw error;
    }
  }

  async createCredential(req: CredentialCreateRequest): Promise<StoredCredential> {
    const response = await api.post<ApiStoredCredentialResponse>('/credentials', {
      name: req.name,
      secret_type: req.secretType,
      data: req.data,
      metadata: req.metadata,
    });
    return {
      id: response.id,
      name: response.name,
      secretType: response.secret_type as SecretType,
      keys: response.keys,
      metadata: response.metadata,
      createdAt: response.created_at,
      updatedAt: response.updated_at,
    };
  }

  async deleteIntegration(id: string): Promise<void> {
    await api.delete(`/integrations/${id}`);
  }

  async testIntegration(id: string): Promise<IntegrationTestResult> {
    return api.post<IntegrationTestResult>(`/integrations/${id}/test`, {});
  }

  async deleteCredential(name: string): Promise<void> {
    await api.delete(`/credentials/${encodeURIComponent(name)}`);
  }

  async getCredentialTypes(): Promise<SecretTypeInfo[]> {
    const response = await api.get<ApiSecretTypeInfoResponse[]>('/credentials/types');
    return response.map(t => ({
      type: t.type as SecretType,
      label: t.label,
      description: t.description,
      fields: t.fields,
      defaultMountType: t.default_mount_type as 'env' | 'file' | 'template',
    }));
  }

  // ── Workspace management ──────────────────────────────────────────

  async listWorkspaces(status?: WorkspaceStatus): Promise<VolundrWorkspace[]> {
    const params = status ? `?status=${status}` : '';
    const response = await api.get<ApiWorkspaceResponse[]>(`/workspaces${params}`);
    return response.map(this.mapWorkspace);
  }

  async listAllWorkspaces(status?: WorkspaceStatus): Promise<VolundrWorkspace[]> {
    const params = status ? `?status=${status}` : '';
    const response = await api.get<ApiWorkspaceResponse[]>(`/admin/workspaces${params}`);
    return response.map(this.mapWorkspace);
  }

  async restoreWorkspace(_id: string): Promise<void> {
    // No-op: restore is handled by StorageContributor on session create.
  }

  async deleteWorkspace(id: string): Promise<void> {
    // id is the session_id — the backend identifies workspaces by session
    await api.delete(`/workspaces/${id}`);
  }

  async bulkDeleteWorkspaces(
    sessionIds: string[]
  ): Promise<{ deleted: number; failed: Array<{ session_id: string; error: string }> }> {
    return api.post<{ deleted: number; failed: Array<{ session_id: string; error: string }> }>(
      '/workspaces/bulk-delete',
      { session_ids: sessionIds }
    );
  }

  async getAdminSettings(): Promise<AdminSettings> {
    const response = await api.get<{
      storage: { home_enabled: boolean; file_manager_enabled: boolean };
    }>('/admin/settings');
    return {
      storage: {
        homeEnabled: response.storage.home_enabled,
        fileManagerEnabled: response.storage.file_manager_enabled ?? true,
      },
    };
  }

  async updateAdminSettings(data: { storage?: AdminStorageSettings }): Promise<AdminSettings> {
    const body: Record<string, unknown> = {};
    if (data.storage) {
      body.storage = {
        home_enabled: data.storage.homeEnabled,
        file_manager_enabled: data.storage.fileManagerEnabled,
      };
    }
    const response = await api.put<{
      storage: { home_enabled: boolean; file_manager_enabled: boolean };
    }>('/admin/settings', body);
    return {
      storage: {
        homeEnabled: response.storage.home_enabled,
        fileManagerEnabled: response.storage.file_manager_enabled ?? true,
      },
    };
  }

  async getFeatureModules(scope?: FeatureScope): Promise<FeatureModule[]> {
    const params = scope ? `?scope=${scope}` : '';
    const response = await api.get<ApiFeatureModuleResponse[]>(`/features${params}`);
    return response.map(f => ({
      key: f.key,
      label: f.label,
      icon: f.icon,
      scope: f.scope as FeatureScope,
      enabled: f.enabled,
      defaultEnabled: f.default_enabled,
      adminOnly: f.admin_only,
      order: f.order,
    }));
  }

  async toggleFeature(key: string, enabled: boolean): Promise<FeatureModule> {
    const f = await api.put<ApiFeatureModuleResponse>(`/features/${key}/toggle`, { enabled });
    return {
      key: f.key,
      label: f.label,
      icon: f.icon,
      scope: f.scope as FeatureScope,
      enabled: f.enabled,
      defaultEnabled: f.default_enabled,
      adminOnly: f.admin_only,
      order: f.order,
    };
  }

  async getUserFeaturePreferences(): Promise<UserFeaturePreference[]> {
    const response = await api.get<ApiUserFeaturePreferenceResponse[]>('/features/preferences');
    return response.map(p => ({
      featureKey: p.feature_key,
      visible: p.visible,
      sortOrder: p.sort_order,
    }));
  }

  async updateUserFeaturePreferences(
    preferences: UserFeaturePreference[]
  ): Promise<UserFeaturePreference[]> {
    const body = {
      preferences: preferences.map(p => ({
        feature_key: p.featureKey,
        visible: p.visible,
        sort_order: p.sortOrder,
      })),
    };
    const response = await api.put<ApiUserFeaturePreferenceResponse[]>(
      '/features/preferences',
      body
    );
    return response.map(p => ({
      featureKey: p.feature_key,
      visible: p.visible,
      sortOrder: p.sort_order,
    }));
  }

  // ── Personal Access Tokens ──────────────────────────────────────

  async listTokens(): Promise<import('@/modules/volundr/models').PersonalAccessToken[]> {
    const response =
      await usersApi.get<
        Array<{ id: string; name: string; created_at: string; last_used_at: string | null }>
      >('/tokens');
    return response.map(t => ({
      id: t.id,
      name: t.name,
      createdAt: t.created_at,
      lastUsedAt: t.last_used_at,
    }));
  }

  async createToken(name: string): Promise<import('@/modules/volundr/models').CreatePATResult> {
    const response = await usersApi.post<{
      id: string;
      name: string;
      token: string;
      created_at: string;
    }>('/tokens', { name });
    return {
      id: response.id,
      name: response.name,
      token: response.token,
      createdAt: response.created_at,
    };
  }

  async revokeToken(id: string): Promise<void> {
    await usersApi.delete(`/tokens/${id}`);
  }

  private mapWorkspace(w: ApiWorkspaceResponse): VolundrWorkspace {
    return {
      id: w.id,
      pvcName: w.pvc_name,
      sessionId: w.session_id,
      ownerId: w.user_id,
      tenantId: w.tenant_id,
      sizeGb: w.size_gb,
      status: w.status,
      createdAt: w.created_at,
      archivedAt: w.archived_at ?? undefined,
      sessionName: w.session_name ?? undefined,
      sourceUrl: w.source_url ?? undefined,
      sourceRef: w.source_ref ?? undefined,
    };
  }

  /**
   * Connect to the SSE stream for real-time updates.
   *
   * Uses fetch() instead of native EventSource so we can attach the
   * Authorization header (EventSource does not support custom headers).
   */
  private connectSSE(): void {
    if (this.sseAbort) {
      return;
    }

    const abort = new AbortController();
    this.sseAbort = abort;

    const headers: Record<string, string> = {
      Accept: 'text/event-stream',
    };
    const token = getAccessToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    fetch(SSE_ENDPOINT, { headers, signal: abort.signal })
      .then(response => {
        if (!response.ok) {
          throw new Error(`SSE request failed: ${response.status}`);
        }
        if (!response.body) {
          throw new Error('SSE response has no body');
        }

        this.reconnectAttempts = 0;
        console.debug('[VolundrService] SSE connected');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        const read = (): void => {
          reader
            .read()
            .then(({ done, value }) => {
              if (done || abort.signal.aborted) {
                return;
              }

              buffer += decoder.decode(value, { stream: true });

              // SSE events are separated by double newlines
              const parts = buffer.split('\n\n');
              // Last part may be incomplete — keep it in the buffer
              buffer = parts.pop() ?? '';

              for (const part of parts) {
                this.handleSSEBlock(part);
              }

              read();
            })
            .catch(err => {
              if (abort.signal.aborted) {
                return;
              }
              console.warn('[VolundrService] SSE read error:', err);
              this.scheduleReconnect();
            });
        };

        read();
      })
      .catch(err => {
        if (abort.signal.aborted) {
          return;
        }
        console.warn('[VolundrService] SSE connection error:', err);
        this.scheduleReconnect();
      });
  }

  /**
   * Parse a single SSE block (lines between double-newlines) and dispatch.
   */
  private handleSSEBlock(block: string): void {
    let eventType = 'message';
    let data = '';

    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) {
        eventType = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        data += line.slice(5).trim();
      }
    }

    if (!data || eventType === 'heartbeat') {
      return;
    }

    try {
      this.dispatchSSEEvent(eventType, data);
    } catch (error) {
      console.error(`[VolundrService] Failed to handle ${eventType} event:`, error);
    }
  }

  /**
   * Dispatch a parsed SSE event to the appropriate handler.
   */
  private dispatchSSEEvent(eventType: string, rawData: string): void {
    switch (eventType) {
      case 'session_created':
      case 'session_updated': {
        const payload: SSESessionPayload = JSON.parse(rawData);
        const session = transformSSESession(payload);
        const idx = this.cachedSessions.findIndex(s => s.id === session.id);
        if (idx === -1) {
          this.cachedSessions = [session, ...this.cachedSessions];
        } else {
          // Merge: preserve fields not carried by SSE (e.g. trackerIssue, taskType)
          const existing = this.cachedSessions[idx];
          const defined = Object.fromEntries(
            Object.entries(session).filter(([, v]) => v !== undefined)
          );
          this.cachedSessions[idx] = {
            ...existing,
            ...defined,
          };
        }
        this.notifySessionSubscribers();
        break;
      }
      case 'session_activity': {
        const payload = JSON.parse(rawData);
        const idx = this.cachedSessions.findIndex(s => s.id === payload.session_id);
        if (idx !== -1) {
          this.cachedSessions[idx] = {
            ...this.cachedSessions[idx],
            activityState: payload.state || null,
          };
          this.notifySessionSubscribers();
        }
        break;
      }
      case 'session_deleted': {
        const payload: SSESessionDeletedPayload = JSON.parse(rawData);
        this.cachedSessions = this.cachedSessions.filter(s => s.id !== payload.id);
        this.notifySessionSubscribers();
        break;
      }
      case 'stats_updated': {
        const payload: SSEStatsPayload = JSON.parse(rawData);
        this.cachedStats = {
          activeSessions: payload.active_sessions,
          totalSessions: payload.total_sessions,
          tokensToday: payload.tokens_today,
          localTokens: payload.local_tokens,
          cloudTokens: payload.cloud_tokens,
          costToday: payload.cost_today,
        };
        this.notifyStatsSubscribers();
        break;
      }
      case 'message_received': {
        const payload: SSEMessagePayload = JSON.parse(rawData);
        const message = transformMessage(payload as unknown as ApiMessageResponse);
        const subscribers = this.messageSubscribers.get(message.sessionId);
        if (subscribers) {
          for (const callback of subscribers) {
            callback({ ...message });
          }
        }
        break;
      }
      case 'log_received': {
        const payload: SSELogPayload = JSON.parse(rawData);
        const log = transformLog(payload as unknown as ApiLogResponse);
        const subscribers = this.logSubscribers.get(log.sessionId);
        if (subscribers) {
          for (const callback of subscribers) {
            callback({ ...log });
          }
        }
        break;
      }
      case 'chronicle_event': {
        const payload: SSEChroniclePayload = JSON.parse(rawData);
        const subscribers = this.chronicleSubscribers.get(payload.session_id);
        if (subscribers) {
          const chronicle = transformChronicle({
            events: [...(payload.event ? [payload.event] : [])],
            files: payload.files,
            commits: payload.commits,
            token_burn: payload.token_burn,
          });
          for (const callback of subscribers) {
            callback(chronicle);
          }
        }
        break;
      }
    }
  }

  /**
   * Disconnect from SSE stream
   */
  private disconnectSSE(): void {
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }

    if (this.sseAbort) {
      this.sseAbort.abort();
      this.sseAbort = null;
    }

    this.reconnectAttempts = 0;
  }

  /**
   * Schedule a reconnection attempt with exponential backoff
   */
  private scheduleReconnect(): void {
    // Close current connection
    if (this.sseAbort) {
      this.sseAbort.abort();
      this.sseAbort = null;
    }

    // Don't reconnect if no subscribers
    if (this.subscribers.size === 0 && this.statsSubscribers.size === 0) {
      return;
    }

    // Calculate delay with exponential backoff
    const delay = Math.min(
      SSE_RECONNECT_BASE_MS * Math.pow(2, this.reconnectAttempts),
      SSE_RECONNECT_MAX_MS
    );

    this.reconnectAttempts++;

    console.debug(
      `[VolundrService] Scheduling SSE reconnect in ${delay}ms (attempt ${this.reconnectAttempts})`
    );

    this.reconnectTimeout = setTimeout(() => {
      this.reconnectTimeout = null;
      this.connectSSE();
    }, delay);
  }

  /**
   * Notify session subscribers with current cached sessions
   */
  private notifySessionSubscribers(): void {
    const sessionsCopy = this.cachedSessions.map(s => ({ ...s }));
    for (const callback of this.subscribers) {
      callback(sessionsCopy);
    }
  }

  /**
   * Notify stats subscribers with current cached stats
   */
  private notifyStatsSubscribers(): void {
    if (!this.cachedStats) {
      return;
    }
    const statsCopy = { ...this.cachedStats };
    for (const callback of this.statsSubscribers) {
      callback(statsCopy);
    }
  }
}
