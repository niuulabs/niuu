/**
 * IVolundrService — port interface for the Völundr session service.
 *
 * Lifted from web/src/modules/volundr/ports/volundr.port.ts and adapted to
 * import from the local models instead of the legacy @/modules/volundr/models
 * aliases.
 */
import type {
  VolundrFeatures,
  VolundrSession,
  VolundrStats,
  VolundrModel,
  VolundrRepo,
  VolundrMessage,
  VolundrLog,
  VolundrAggregatedLog,
  VolundrLogParticipant,
  SessionChronicle,
  ClusterResourceInfo,
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
  StoredCredential,
  CredentialCreateRequest,
  SecretType,
  SecretTypeInfo,
  VolundrWorkspace,
  WorkspaceStatus,
  VolundrMember,
  VolundrProvisioningResult,
  SessionSource,
  SessionDefinition,
  AdminSettings,
  AdminStorageSettings,
  FeatureModule,
  FeatureScope,
  UserFeaturePreference,
  PersonalAccessToken,
  CreatePATResult,
} from '../models/volundr.model';

export interface IVolundrService {
  // Feature flags
  getFeatures(): Promise<VolundrFeatures>;

  // Session definitions
  getSessionDefinitions(): Promise<SessionDefinition[]>;

  // Sessions
  getSessions(): Promise<VolundrSession[]>;
  getSession(id: string): Promise<VolundrSession | null>;
  getActiveSessions(): Promise<VolundrSession[]>;
  getStats(): Promise<VolundrStats>;
  getModels(): Promise<Record<string, VolundrModel>>;
  getRepos(): Promise<VolundrRepo[]>;

  /** Subscribe to live session updates via SSE. Returns an unsubscribe function. */
  subscribe(callback: (sessions: VolundrSession[]) => void): () => void;
  /** Subscribe to live stats updates via SSE. Returns an unsubscribe function. */
  subscribeStats(callback: (stats: VolundrStats) => void): () => void;

  // Templates
  getTemplates(): Promise<VolundrTemplate[]>;
  getTemplate(name: string): Promise<VolundrTemplate | null>;
  saveTemplate(template: VolundrTemplate): Promise<VolundrTemplate>;

  // Presets
  getPresets(): Promise<VolundrPreset[]>;
  getPreset(id: string): Promise<VolundrPreset | null>;
  savePreset(
    preset: Omit<VolundrPreset, 'id' | 'createdAt' | 'updatedAt'> & { id?: string },
  ): Promise<VolundrPreset>;
  deletePreset(id: string): Promise<void>;

  // Cluster resources
  getAvailableMcpServers(): Promise<McpServerConfig[]>;
  getAvailableSecrets(): Promise<string[]>;
  createSecret(
    name: string,
    data: Record<string, string>,
  ): Promise<{ name: string; keys: string[] }>;
  getClusterResources(): Promise<ClusterResourceInfo>;

  // Session lifecycle
  startSession(config: {
    name: string;
    source: SessionSource;
    model: string;
    templateName?: string;
    presetId?: string;
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
    workloadConfig?: Record<string, string | number | boolean | undefined>;
  }): Promise<VolundrSession>;
  connectSession(config: { name: string; hostname: string }): Promise<VolundrSession>;
  updateSession(
    sessionId: string,
    updates: { name?: string; model?: string; branch?: string; tracker_issue_id?: string },
  ): Promise<VolundrSession>;
  stopSession(sessionId: string): Promise<void>;
  resumeSession(sessionId: string): Promise<void>;
  deleteSession(sessionId: string, cleanup?: string[]): Promise<void>;
  archiveSession(sessionId: string): Promise<void>;
  restoreSession(sessionId: string): Promise<void>;
  listArchivedSessions(): Promise<VolundrSession[]>;

  // Messaging
  getMessages(sessionId: string): Promise<VolundrMessage[]>;
  sendMessage(sessionId: string, content: string): Promise<VolundrMessage>;
  subscribeMessages(sessionId: string, callback: (message: VolundrMessage) => void): () => void;

  // Logs
  getLogs(sessionId: string, limit?: number): Promise<VolundrLog[]>;
  subscribeLogs(sessionId: string, callback: (log: VolundrLog) => void): () => void;
  getAggregatedLogs(
    sessionId: string,
    options?: {
      limit?: number;
      level?: string;
      participants?: string[];
      query?: string;
    },
  ): Promise<{ lines: VolundrAggregatedLog[]; participants: VolundrLogParticipant[] }>;
  subscribeAggregatedLogs(
    sessionId: string,
    options: {
      limit?: number;
      level?: string;
      participants?: string[];
      query?: string;
    },
    callback: (payload: {
      lines: VolundrAggregatedLog[];
      participants: VolundrLogParticipant[];
    }) => void,
  ): () => void;

  // Code server
  getCodeServerUrl(sessionId: string): Promise<string | null>;

  // Chronicle
  getChronicle(sessionId: string): Promise<SessionChronicle | null>;
  subscribeChronicle(
    sessionId: string,
    callback: (chronicle: SessionChronicle) => void,
  ): () => void;

  // Pull requests / CI
  getPullRequests(repoUrl: string, status?: string): Promise<PullRequest[]>;
  createPullRequest(sessionId: string, title?: string, targetBranch?: string): Promise<PullRequest>;
  mergePullRequest(prNumber: number, repoUrl: string, mergeMethod?: string): Promise<MergeResult>;
  getCIStatus(prNumber: number, repoUrl: string, branch: string): Promise<CIStatusValue>;

  // MCP servers
  getSessionMcpServers(sessionId: string): Promise<McpServer[]>;

  // Tracker
  searchTrackerIssues(query: string, projectId?: string): Promise<TrackerIssue[]>;
  getProjectRepoMappings(): Promise<ProjectRepoMapping[]>;
  updateTrackerIssueStatus(issueId: string, status: TrackerIssue['status']): Promise<TrackerIssue>;

  // Identity / users
  getIdentity(): Promise<VolundrIdentity>;
  listUsers(): Promise<VolundrUser[]>;

  // Tenants
  getTenants(): Promise<VolundrTenant[]>;
  getTenant(id: string): Promise<VolundrTenant | null>;
  createTenant(data: {
    name: string;
    tier: string;
    maxSessions: number;
    maxStorageGb: number;
  }): Promise<VolundrTenant>;
  deleteTenant(id: string): Promise<void>;
  updateTenant(
    id: string,
    data: { tier?: string; maxSessions?: number; maxStorageGb?: number },
  ): Promise<VolundrTenant>;
  getTenantMembers(tenantId: string): Promise<VolundrMember[]>;
  reprovisionUser(userId: string): Promise<VolundrProvisioningResult>;
  reprovisionTenant(tenantId: string): Promise<VolundrProvisioningResult[]>;

  // Credentials (legacy)
  getUserCredentials(): Promise<VolundrCredential[]>;
  storeUserCredential(name: string, data: Record<string, string>): Promise<void>;
  deleteUserCredential(name: string): Promise<void>;
  getTenantCredentials(): Promise<VolundrCredential[]>;
  storeTenantCredential(name: string, data: Record<string, string>): Promise<void>;
  deleteTenantCredential(name: string): Promise<void>;

  // Integrations
  getIntegrationCatalog(): Promise<CatalogEntry[]>;
  getIntegrations(): Promise<IntegrationConnection[]>;
  createIntegration(
    connection: Omit<IntegrationConnection, 'id' | 'createdAt' | 'updatedAt'>,
  ): Promise<IntegrationConnection>;
  deleteIntegration(id: string): Promise<void>;
  testIntegration(id: string): Promise<IntegrationTestResult>;

  // Pluggable credential store
  getCredentials(type?: SecretType): Promise<StoredCredential[]>;
  getCredential(name: string): Promise<StoredCredential | null>;
  createCredential(req: CredentialCreateRequest): Promise<StoredCredential>;
  deleteCredential(name: string): Promise<void>;
  getCredentialTypes(): Promise<SecretTypeInfo[]>;

  // Workspaces
  listWorkspaces(status?: WorkspaceStatus): Promise<VolundrWorkspace[]>;
  listAllWorkspaces(status?: WorkspaceStatus): Promise<VolundrWorkspace[]>;
  restoreWorkspace(id: string): Promise<void>;
  deleteWorkspace(id: string): Promise<void>;
  bulkDeleteWorkspaces(
    sessionIds: string[],
  ): Promise<{ deleted: number; failed: Array<{ session_id: string; error: string }> }>;

  // Admin
  getAdminSettings(): Promise<AdminSettings>;
  updateAdminSettings(data: { storage?: AdminStorageSettings }): Promise<AdminSettings>;

  // Feature modules
  getFeatureModules(scope?: FeatureScope): Promise<FeatureModule[]>;
  toggleFeature(key: string, enabled: boolean): Promise<FeatureModule>;
  getUserFeaturePreferences(): Promise<UserFeaturePreference[]>;
  updateUserFeaturePreferences(
    preferences: UserFeaturePreference[],
  ): Promise<UserFeaturePreference[]>;

  // Personal Access Tokens
  listTokens(): Promise<PersonalAccessToken[]>;
  createToken(name: string): Promise<CreatePATResult>;
  revokeToken(id: string): Promise<void>;
}
