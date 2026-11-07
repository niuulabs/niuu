/**
 * IVolundrService — port interface for the Völundr session service.
 *
 * Lifted from web/src/modules/volundr/ports/volundr.port.ts and adapted to
 * import from the local models instead of the legacy @/modules/volundr/models
 * aliases.
 */
import type { SecretFulfillment, SecretRequest } from '@niuulabs/domain';
import type { LivePreviewConfig } from '../domain/livePreview';
import type { SessionRuntimeOffer } from '../domain/sessionRuntimes';
import type {
  ComputeHost,
  ComputeHostChanges,
  ComputeHostsOverview,
  NewPairingCode,
  PairingCodeRequest,
  RemoveHostResult,
} from '../domain/computeHosts';
import type {
  VolundrFeatures,
  VolundrSession,
  VolundrStats,
  VolundrRepo,
  VolundrMessage,
  SessionMessageDelivery,
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
  VolundrLaunchSpec,
  LaunchScope,
  TrackerIssue,
  ProjectRepoMapping,
  ForgeProject,
  ForgeProjectChanges,
  SessionProjectMembership,
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
  VolundrTarget,
  AdminSettings,
  AdminStorageSettings,
  FeatureModule,
  FeatureScope,
  UserFeaturePreference,
  PersonalAccessToken,
  CreatePATResult,
  VolundrWorkflowGate,
  VolundrSessionTrace,
  VolundrSessionTraceSummary,
  ExternalSession,
  SessionReadState,
  SessionReadStateChange,
  ForgeMcpSessionOptions,
} from '../models/volundr.model';

export interface VolundrConversationTurn {
  id: string;
  role: string;
  content: string;
  parts?: Array<Record<string, unknown>>;
  created_at: string;
  metadata?: Record<string, unknown>;
  participant_id?: string;
  participant_meta?: Record<string, unknown>;
  thread_id?: string;
  visibility?: 'visible' | 'internal';
}

export interface VolundrConversationHistory {
  turns: VolundrConversationTurn[];
  last_activity?: string;
}

export type PermissionAutoApprovalReason =
  'allowed' | 'disabled' | 'no_command' | 'denylist' | 'no_allowlist_match' | 'endpoint_error';

export interface PermissionAutoApprovalRequest {
  requestId: string;
  toolName: string;
  description: string;
  command?: string;
  input?: Record<string, unknown>;
}

export interface PermissionAutoApprovalDecision {
  canAutoApprove: boolean;
  reason: PermissionAutoApprovalReason;
  command: string | null;
  delaySeconds: number;
  matchedPattern?: string | null;
}

export interface ResolveWorkflowGateRequest {
  decision: 'APPROVE' | 'CHANGES_REQUESTED';
  notes?: string;
  source?: string;
}

export interface UserHomeListing {
  status: 'starting' | 'ready';
  detail?: string;
  path?: string;
  capacity_bytes?: number;
  available_bytes?: number;
  entries?: Array<{
    name: string;
    path: string;
    kind: 'directory' | 'file' | 'symlink';
    size: number;
  }>;
}

export interface SessionReadOptions {
  instanceId?: string;
  signal?: AbortSignal;
}

export interface SessionChangesReadOptions {
  instanceId: string;
  archived: boolean;
  signal?: AbortSignal;
}

/** One host's sessions written since a cursor, and every id that host lists now. */
export interface VolundrSessionChanges<T = VolundrSession> {
  /** Opaque; send it back as the next read's cursor ("0" reads everything). */
  cursor: string;
  ids: string[];
  sessions: T[];
}

export interface RuntimeVersion {
  state: 'current' | 'different' | 'unknown' | 'unavailable' | 'not_running';
  current: { revision?: string; build?: string; source_sha256?: string; dirty?: boolean } | null;
  available: { revision?: string; build?: string; source_sha256?: string; dirty?: boolean } | null;
}

export interface IVolundrService {
  // Feature flags
  getFeatures(instanceId?: string): Promise<VolundrFeatures>;
  /**
   * Where sessions' live previews open; disabled when the platform serves none. Each Forge
   * host has its own preview domain, so ask the one that runs the session.
   */
  getLivePreviewConfig(instanceId?: string): Promise<LivePreviewConfig>;
  /**
   * What a launch on the host may run as ("Runs as"), with warm readiness. Empty when the
   * host offers no choice. Each Forge host offers its own, so ask the one that will run it.
   */
  getSessionRuntimes(instanceId?: string): Promise<SessionRuntimeOffer[]>;

  // Compute hosts: machines registered as compute for a Forge. Admin only (`volundr:admin`).
  /**
   * The Forge's registered hosts, its pool summary, connectors, runtimes and open pairing
   * codes. Null when the Forge has no compute hosts configured (it answers 404): that
   * Forge has no Hosts view. Any other failure rejects.
   */
  getComputeHosts(instanceId: string): Promise<ComputeHostsOverview | null>;
  /**
   * Distinct tag sets of the hosts that can take a session now (healthy, active, slots
   * set), for targeting. Null for a Forge without compute hosts.
   */
  getComputeHostTagSets(instanceId: string): Promise<string[][] | null>;
  /** Mints a single-use pairing code and the one-line command that joins a host with it. */
  createPairingCode(instanceId: string, request: PairingCodeRequest): Promise<NewPairingCode>;
  /** Revokes a code that has not been used. */
  revokePairingCode(instanceId: string, codeId: string): Promise<void>;
  /**
   * Changes a host's tags, slot count or name. Rejects with the platform's own reason when
   * the slot count needs the host out of service or is over what the host can hold.
   */
  updateComputeHost(
    instanceId: string,
    hostId: string,
    changes: ComputeHostChanges,
  ): Promise<ComputeHost>;
  /**
   * Takes no new sessions; running ones are never stopped. With `remove`, Forge removes
   * the host once it is empty.
   */
  drainComputeHost(instanceId: string, hostId: string, remove: boolean): Promise<ComputeHost>;
  /** Returns an out-of-service host to service after a clean audit. */
  resumeComputeHost(instanceId: string, hostId: string): Promise<ComputeHost>;
  /** Runs the probe and the audit now. */
  checkComputeHost(instanceId: string, hostId: string): Promise<ComputeHost>;
  /** Removes an empty host; rejects while sessions still run on it (drain and remove). */
  removeComputeHost(instanceId: string, hostId: string): Promise<RemoveHostResult>;

  // Session definitions
  getSessionDefinitions(): Promise<SessionDefinition[]>;

  // Sessions
  getSessions(options?: SessionReadOptions): Promise<VolundrSession[]>;
  getSession(id: string, options?: SessionReadOptions): Promise<VolundrSession | null>;
  /** Only adapters that can read one host incrementally implement this. */
  getSessionChanges?(
    cursor: string,
    options: SessionChangesReadOptions,
  ): Promise<VolundrSessionChanges>;
  getActiveSessions(): Promise<VolundrSession[]>;
  /** The caller's read state for a session's latest reply. */
  getSessionReadState(sessionId: string, options?: SessionReadOptions): Promise<SessionReadState>;
  /**
   * Marks the session read or unread. Rejects with `SessionReadStateConflictError` when a newer
   * action won; the caller reads the state again before deciding.
   */
  changeSessionReadState(
    sessionId: string,
    change: SessionReadStateChange,
    options?: SessionReadOptions,
  ): Promise<SessionReadState>;
  getStats(options?: SessionReadOptions): Promise<VolundrStats>;
  getRepos(): Promise<VolundrRepo[]>;
  getTargets(): Promise<VolundrTarget[]>;
  listUserHome(instanceId: string, path: string): Promise<UserHomeListing>;
  deleteUserHomePath(instanceId: string, path: string): Promise<void>;
  getProjects(options?: SessionReadOptions): Promise<ForgeProject[]>;
  /** A name is enough. The host is the given instance, else the default one. */
  createProject(name: string, options?: { instanceId?: string }): Promise<ForgeProject>;
  /**
   * Change a project at the revision the caller read; a stale revision is refused. The change
   * goes to the host that owns the project. Setting `workspace_path` connects a checkout.
   */
  updateProject(project: ForgeProject, changes: ForgeProjectChanges): Promise<ForgeProject>;
  getSessionProject(
    sessionId: string,
    options?: SessionReadOptions,
  ): Promise<SessionProjectMembership>;
  assignSessionProject(
    sessionId: string,
    assignment: { projectId: string; projectInstanceId: string; expectedRevision: number },
    options?: SessionReadOptions,
  ): Promise<SessionProjectMembership>;

  /** Subscribe to live session updates via SSE. Returns an unsubscribe function. */
  subscribe(
    callback: (sessions: VolundrSession[]) => void,
    options?: { hydrate?: boolean },
  ): () => void;
  /** Subscribe to live stats updates via SSE. Returns an unsubscribe function. */
  subscribeStats(callback: (stats: VolundrStats) => void): () => void;

  // Launch specs (scope = system | user)
  getLaunchSpecs(scope?: LaunchScope): Promise<VolundrLaunchSpec[]>;
  getLaunchSpec(ref: string): Promise<VolundrLaunchSpec | null>;
  saveLaunchSpec(
    spec: Omit<VolundrLaunchSpec, 'id' | 'scope' | 'createdAt' | 'updatedAt'> & { id?: string },
  ): Promise<VolundrLaunchSpec>;
  deleteLaunchSpec(id: string): Promise<void>;

  // Cluster resources
  getAvailableMcpServers(): Promise<McpServerConfig[]>;
  getAvailableSecrets(): Promise<string[]>;
  createSecret(
    name: string,
    data: Record<string, string>,
  ): Promise<{ name: string; keys: string[] }>;
  getClusterResources(options?: SessionReadOptions): Promise<ClusterResourceInfo>;

  // Session lifecycle
  startSession(config: {
    name: string;
    personaName?: string;
    source: SessionSource;
    model: string;
    launchSpec?: string;
    launchSpecId?: string;
    definition?: string;
    taskType?: string;
    trackerIssue?: TrackerIssue;
    terminalRestricted?: boolean;
    instanceId?: string | null;
    targetTags?: string[];
    targetMatch?: 'all' | 'any';
    workspaceId?: string;
    credentialNames?: string[];
    integrationIds?: string[];
    resourceConfig?: Record<string, string | undefined>;
    systemPrompt?: string;
    initialPrompt?: string;
    /** What the session runs: an ordinary coding session (default) or `ravn_flock`. */
    workloadType?: 'session' | 'ravn_flock';
    workloadConfig?: Record<string, unknown>;
    /** Forge MCP grants for the session; the node's defaults and role grants apply on top. */
    forgeMcp?: ForgeMcpSessionOptions;
  }): Promise<VolundrSession>;
  evaluatePermissionAutoApproval(
    sessionId: string,
    request: PermissionAutoApprovalRequest,
  ): Promise<PermissionAutoApprovalDecision>;
  connectSession(config: { name: string; hostname: string }): Promise<VolundrSession>;
  updateSession(
    sessionId: string,
    updates: { name?: string; model?: string; branch?: string; tracker_issue_id?: string },
  ): Promise<VolundrSession>;
  /**
   * Forks a session into a child on the same host, as Lexi's side chat: Forge's reforge of the
   * session's chronicle (same repository, branch and model), named `name` and started. Throws
   * `SessionForkError` with the child's id when the child was created but a later step failed.
   */
  forkSession(sessionId: string, name: string): Promise<VolundrSession>;
  getRuntimeVersion(sessionId: string, instanceId?: string): Promise<RuntimeVersion>;
  stopSession(sessionId: string): Promise<void>;
  resumeSession(sessionId: string): Promise<void>;
  deleteSession(sessionId: string, cleanup?: string[]): Promise<void>;
  archiveSession(sessionId: string): Promise<void>;
  archiveStoppedSessions(): Promise<string[]>;

  // Secure secret requests: an agent asks the person for a secret. The value goes to Forge in
  // `fulfillSecretRequest` and is never returned, cached or shown to the agent.
  getSecretRequest(sessionId: string, requestId: string): Promise<SecretRequest>;
  fulfillSecretRequest(
    sessionId: string,
    requestId: string,
    fulfillment: SecretFulfillment,
  ): Promise<SecretRequest>;
  declineSecretRequest(sessionId: string, requestId: string): Promise<SecretRequest>;
  restoreSession(sessionId: string): Promise<void>;
  listArchivedSessions(options?: SessionReadOptions): Promise<VolundrSession[]>;

  // External CLI sessions (Claude Code / Codex discovered on the host).
  // listExternalSessions rejects with a 503-status error when discovery is
  // not enabled on the server — callers treat that as "feature unavailable".
  listExternalSessions(): Promise<ExternalSession[]>;
  importExternalSession(
    provider: string,
    externalId: string,
    name?: string,
  ): Promise<VolundrSession>;

  // Messaging
  getConversationHistory(sessionId: string): Promise<VolundrConversationHistory>;
  /** One turn in full (`GET /sessions/{id}/conversation/turns/{turnId}`), wherever it sits. */
  getConversationTurn(sessionId: string, turnId: string): Promise<VolundrConversationTurn>;
  getWorkflowGates(sessionId: string): Promise<VolundrWorkflowGate[]>;
  resolveWorkflowGate(
    sessionId: string,
    gateId: string,
    request: ResolveWorkflowGateRequest,
  ): Promise<VolundrWorkflowGate>;
  getMessages(sessionId: string): Promise<VolundrMessage[]>;
  /**
   * Send a user message into a running session. Resolves `delivered` or `pending` (accepted,
   * delivery still being retried; not confirmed); rejects with Forge's `detail` on refusal.
   */
  sendMessage(sessionId: string, content: string): Promise<SessionMessageDelivery>;
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
  getSessionTrace(sessionId: string): Promise<VolundrSessionTrace | null>;
  getSessionTraceSummary(sessionId: string): Promise<VolundrSessionTraceSummary | null>;

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
  createToken(name: string, scopes?: string[]): Promise<CreatePATResult>;
  revokeToken(id: string): Promise<void>;
}
