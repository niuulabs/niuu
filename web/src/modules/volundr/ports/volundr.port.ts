import type {
  VolundrFeatures,
  VolundrSession,
  VolundrStats,
  VolundrModel,
  VolundrRepo,
  VolundrMessage,
  VolundrLog,
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
  AdminSettings,
  AdminStorageSettings,
  FeatureModule,
  FeatureScope,
  UserFeaturePreference,
  PersonalAccessToken,
  CreatePATResult,
  SessionDefinition,
} from '@/modules/volundr/models';

/**
 * Port interface for Völundr service
 * Manages Claude Code sessions
 */
export interface IVolundrService {
  /**
   * Get available session definitions from the server
   */
  getSessionDefinitions(): Promise<SessionDefinition[]>;

  /**
   * Get feature flags from the server
   */
  getFeatures(): Promise<VolundrFeatures>;

  /**
   * Get all sessions
   */
  getSessions(): Promise<VolundrSession[]>;

  /**
   * Get a specific session by ID
   */
  getSession(id: string): Promise<VolundrSession | null>;

  /**
   * Get active sessions only
   */
  getActiveSessions(): Promise<VolundrSession[]>;

  /**
   * Get Völundr statistics
   */
  getStats(): Promise<VolundrStats>;

  /**
   * Get available models
   */
  getModels(): Promise<Record<string, VolundrModel>>;

  /**
   * Get available repositories
   */
  getRepos(): Promise<VolundrRepo[]>;

  /**
   * Subscribe to session updates via SSE
   * @returns Unsubscribe function
   */
  subscribe(callback: (sessions: VolundrSession[]) => void): () => void;

  /**
   * Subscribe to stats updates via SSE
   * @returns Unsubscribe function
   */
  subscribeStats(callback: (stats: VolundrStats) => void): () => void;

  /**
   * Get all workspace templates
   */
  getTemplates(): Promise<VolundrTemplate[]>;

  /**
   * Get a specific template by name
   */
  getTemplate(name: string): Promise<VolundrTemplate | null>;

  /**
   * Save a template (create or update)
   */
  saveTemplate(template: VolundrTemplate): Promise<VolundrTemplate>;

  /**
   * Get all runtime presets
   */
  getPresets(): Promise<VolundrPreset[]>;

  /**
   * Get a specific preset by ID
   */
  getPreset(id: string): Promise<VolundrPreset | null>;

  /**
   * Save a preset (create or update)
   */
  savePreset(
    preset: Omit<VolundrPreset, 'id' | 'createdAt' | 'updatedAt'> & { id?: string }
  ): Promise<VolundrPreset>;

  /**
   * Delete a preset by ID
   */
  deletePreset(id: string): Promise<void>;

  /**
   * Get available MCP server configurations
   */
  getAvailableMcpServers(): Promise<McpServerConfig[]>;

  /**
   * Get available Kubernetes secret names
   */
  getAvailableSecrets(): Promise<string[]>;

  /**
   * Create a new Kubernetes secret
   */
  createSecret(
    name: string,
    data: Record<string, string>
  ): Promise<{ name: string; keys: string[] }>;

  /**
   * Get cluster resource types and capacity
   */
  getClusterResources(): Promise<ClusterResourceInfo>;

  /**
   * Start a new session
   */
  startSession(config: {
    name: string;
    source: SessionSource;
    model: string;
    templateName?: string;
    taskType?: string;
    definition?: string;
    trackerIssue?: TrackerIssue;
    terminalRestricted?: boolean;
    workspaceId?: string;
    credentialNames?: string[];
    integrationIds?: string[];
    resourceConfig?: Record<string, string | undefined>;
    systemPrompt?: string;
    initialPrompt?: string;
  }): Promise<VolundrSession>;

  /**
   * Connect to an existing external session by hostname.
   * Creates a manual session entry that can be connected/disconnected
   * but is not managed by the backend.
   */
  connectSession(config: { name: string; hostname: string }): Promise<VolundrSession>;

  /**
   * Update a session (e.g. rename)
   */
  updateSession(
    sessionId: string,
    updates: { name?: string; model?: string; branch?: string; tracker_issue_id?: string }
  ): Promise<VolundrSession>;

  /**
   * Stop a running session
   */
  stopSession(sessionId: string): Promise<void>;

  /**
   * Resume a stopped session
   */
  resumeSession(sessionId: string): Promise<void>;

  /**
   * Delete a session
   */
  deleteSession(sessionId: string, cleanup?: string[]): Promise<void>;

  /**
   * Archive a session (stops it first if running)
   */
  archiveSession(sessionId: string): Promise<void>;

  /**
   * Restore an archived session back to stopped state
   */
  restoreSession(sessionId: string): Promise<void>;

  /**
   * List all archived sessions
   */
  listArchivedSessions(): Promise<VolundrSession[]>;

  /**
   * Get messages for a session
   */
  getMessages(sessionId: string): Promise<VolundrMessage[]>;

  /**
   * Send a message to a session
   * Returns the assistant's response message
   */
  sendMessage(sessionId: string, content: string): Promise<VolundrMessage>;

  /**
   * Subscribe to new messages for a session
   * @returns Unsubscribe function
   */
  subscribeMessages(sessionId: string, callback: (message: VolundrMessage) => void): () => void;

  /**
   * Get logs for a session
   */
  getLogs(sessionId: string, limit?: number): Promise<VolundrLog[]>;

  /**
   * Subscribe to log updates for a session
   * @returns Unsubscribe function
   */
  subscribeLogs(sessionId: string, callback: (log: VolundrLog) => void): () => void;

  /**
   * Get the code-server URL for a session
   * Returns null if session is not running
   */
  getCodeServerUrl(sessionId: string): Promise<string | null>;

  /**
   * Get chronicle timeline for a session
   */
  getChronicle(sessionId: string): Promise<SessionChronicle | null>;

  /**
   * Subscribe to chronicle updates for a session via SSE
   * @returns Unsubscribe function
   */
  subscribeChronicle(
    sessionId: string,
    callback: (chronicle: SessionChronicle) => void
  ): () => void;

  /**
   * List pull requests for a repository
   */
  getPullRequests(repoUrl: string, status?: string): Promise<PullRequest[]>;

  /**
   * Create a pull request from a session
   */
  createPullRequest(sessionId: string, title?: string, targetBranch?: string): Promise<PullRequest>;

  /**
   * Merge a pull request
   */
  mergePullRequest(prNumber: number, repoUrl: string, mergeMethod?: string): Promise<MergeResult>;

  /**
   * Get CI status for a PR's branch
   */
  getCIStatus(prNumber: number, repoUrl: string, branch: string): Promise<CIStatusValue>;

  /**
   * Get MCP servers connected to a session
   */
  getSessionMcpServers(sessionId: string): Promise<McpServer[]>;

  /**
   * Search Tracker issues by query string, optionally scoped to a project
   */
  searchTrackerIssues(query: string, projectId?: string): Promise<TrackerIssue[]>;

  /**
   * Get mappings between tracker projects and git repositories
   */
  getProjectRepoMappings(): Promise<ProjectRepoMapping[]>;

  /**
   * Update the status of a Tracker issue
   */
  updateTrackerIssueStatus(issueId: string, status: TrackerIssue['status']): Promise<TrackerIssue>;

  /**
   * Get the current authenticated user's identity
   */
  getIdentity(): Promise<VolundrIdentity>;

  /**
   * List all users (admin only)
   */
  listUsers(): Promise<VolundrUser[]>;

  /**
   * List tenants visible to the current user
   */
  getTenants(): Promise<VolundrTenant[]>;

  /**
   * Get a specific tenant by ID
   */
  getTenant(id: string): Promise<VolundrTenant | null>;

  /**
   * Create a new tenant (admin only)
   */
  createTenant(data: {
    name: string;
    tier: string;
    maxSessions: number;
    maxStorageGb: number;
  }): Promise<VolundrTenant>;

  /**
   * Delete a tenant (admin only)
   */
  deleteTenant(id: string): Promise<void>;

  /**
   * Update tenant settings (admin only)
   */
  updateTenant(
    id: string,
    data: {
      tier?: string;
      maxSessions?: number;
      maxStorageGb?: number;
    }
  ): Promise<VolundrTenant>;

  /**
   * Get members of a tenant
   */
  getTenantMembers(tenantId: string): Promise<VolundrMember[]>;

  /**
   * Reprovision a user's storage
   */
  reprovisionUser(userId: string): Promise<VolundrProvisioningResult>;

  /**
   * Reprovision all users in a tenant
   */
  reprovisionTenant(tenantId: string): Promise<VolundrProvisioningResult[]>;

  // Credential management

  /**
   * List credentials stored for the current user
   */
  getUserCredentials(): Promise<VolundrCredential[]>;

  /**
   * Store a credential for the current user
   */
  storeUserCredential(name: string, data: Record<string, string>): Promise<void>;

  /**
   * Delete a credential for the current user
   */
  deleteUserCredential(name: string): Promise<void>;

  /**
   * List credentials stored for the current tenant
   */
  getTenantCredentials(): Promise<VolundrCredential[]>;

  /**
   * Store a credential for the current tenant
   */
  storeTenantCredential(name: string, data: Record<string, string>): Promise<void>;

  /**
   * Delete a credential for the current tenant
   */
  deleteTenantCredential(name: string): Promise<void>;

  // Integration management

  /**
   * Get the integration catalog (available integration definitions)
   */
  getIntegrationCatalog(): Promise<CatalogEntry[]>;

  /**
   * List the current user's integration connections
   */
  getIntegrations(): Promise<IntegrationConnection[]>;

  /**
   * Create a new integration connection
   */
  createIntegration(
    connection: Omit<IntegrationConnection, 'id' | 'createdAt' | 'updatedAt'>
  ): Promise<IntegrationConnection>;

  /**
   * Delete an integration connection
   */
  deleteIntegration(id: string): Promise<void>;

  /**
   * Test an integration connection
   */
  testIntegration(id: string): Promise<IntegrationTestResult>;

  // Pluggable credential store (new API)

  /**
   * List credentials with full metadata from the pluggable store
   */
  getCredentials(type?: SecretType): Promise<StoredCredential[]>;

  /**
   * Get a single credential's metadata by name
   */
  getCredential(name: string): Promise<StoredCredential | null>;

  /**
   * Create a credential in the pluggable store
   */
  createCredential(req: CredentialCreateRequest): Promise<StoredCredential>;

  /**
   * Delete a credential from the pluggable store
   */
  deleteCredential(name: string): Promise<void>;

  /**
   * Get available credential types with field definitions
   */
  getCredentialTypes(): Promise<SecretTypeInfo[]>;

  // Workspace management

  /**
   * List workspaces for the current user, optionally filtered by status
   */
  listWorkspaces(status?: WorkspaceStatus): Promise<VolundrWorkspace[]>;

  /**
   * List all workspaces across all users (admin only)
   */
  listAllWorkspaces(status?: WorkspaceStatus): Promise<VolundrWorkspace[]>;

  /**
   * Restore an archived workspace
   */
  restoreWorkspace(id: string): Promise<void>;

  /**
   * Delete a workspace
   */
  deleteWorkspace(id: string): Promise<void>;

  /**
   * Delete multiple workspaces by session IDs
   */
  bulkDeleteWorkspaces(
    sessionIds: string[]
  ): Promise<{ deleted: number; failed: Array<{ session_id: string; error: string }> }>;

  // Admin settings

  /**
   * Get admin settings (admin only)
   */
  getAdminSettings(): Promise<AdminSettings>;

  /**
   * Update admin settings (admin only)
   */
  updateAdminSettings(data: { storage?: AdminStorageSettings }): Promise<AdminSettings>;

  // Feature modules

  /**
   * Get the feature module catalog, optionally filtered by scope
   */
  getFeatureModules(scope?: FeatureScope): Promise<FeatureModule[]>;

  /**
   * Admin: toggle a feature on/off globally
   */
  toggleFeature(key: string, enabled: boolean): Promise<FeatureModule>;

  /**
   * Get the current user's feature layout preferences
   */
  getUserFeaturePreferences(): Promise<UserFeaturePreference[]>;

  /**
   * Update the current user's feature layout preferences
   */
  updateUserFeaturePreferences(
    preferences: UserFeaturePreference[]
  ): Promise<UserFeaturePreference[]>;

  // Personal Access Tokens

  /**
   * List the current user's personal access tokens
   */
  listTokens(): Promise<PersonalAccessToken[]>;

  /**
   * Create a new personal access token
   */
  createToken(name: string): Promise<CreatePATResult>;

  /**
   * Revoke a personal access token by ID
   */
  revokeToken(id: string): Promise<void>;
}
