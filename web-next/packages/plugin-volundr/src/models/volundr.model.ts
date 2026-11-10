/**
 * Völundr service models — lifted from web/src/modules/volundr/models/volundr.model.ts
 * and adapted to import from @niuulabs/plugin-sdk instead of the legacy @/ aliases.
 */
import type {
  AppIdentity,
  FeatureScope,
  FeatureModule,
  UserFeaturePreference,
} from '@niuulabs/plugin-sdk';

// ---------------------------------------------------------------------------
// Legacy session status — used by the IVolundrService REST API surface.
// Distinct from the new domain SessionState (domain/session.ts).
// ---------------------------------------------------------------------------

export type SessionStatus =
  | 'created'
  | 'starting'
  | 'provisioning'
  | 'running'
  | 'stopping'
  | 'stopped'
  | 'failed'
  | 'error'
  | 'archived';

// ---------------------------------------------------------------------------
// Feature flags
// ---------------------------------------------------------------------------

export interface VolundrFeatures {
  localMountsEnabled: boolean;
  fileManagerEnabled: boolean;
  miniMode: boolean;
}

// ---------------------------------------------------------------------------
// Cluster resource info (legacy REST shape)
// ---------------------------------------------------------------------------

export interface ResourceType {
  name: string;
  resourceKey: string;
  displayName: string;
  unit: string;
  category: string;
}

export interface NodeResourceSummary {
  name: string;
  instanceId?: string;
  instance_id?: string;
  instanceName?: string;
  instance_name?: string;
  instanceSlug?: string;
  instance_slug?: string;
  labels: Record<string, string>;
  allocatable: Record<string, string>;
  allocated: Record<string, string>;
  available: Record<string, string>;
}

export interface ClusterResourceInfo {
  instances?: Array<{
    id: string;
    name: string;
    slug: string;
    isDefault?: boolean;
    is_default?: boolean;
    tags?: string[];
  }>;
  resourceTypes: ResourceType[];
  nodes: NodeResourceSummary[];
}

// ---------------------------------------------------------------------------
// Model / repo info
// ---------------------------------------------------------------------------

export type ModelTier = 'frontier' | 'balanced' | 'execution' | 'reasoning';
export type ModelProvider = 'cloud' | 'local';
export type SessionOrigin = 'managed' | 'manual' | 'volundr' | 'claude' | 'codex';

export interface VolundrModel {
  name: string;
  provider: ModelProvider;
  vendor?: string;
  tier: ModelTier;
  color: string;
  cost?: string;
  vram?: string;
  sessionDefinition?: string;
}

export interface VolundrRepo {
  provider: 'github' | 'gitlab' | 'bitbucket';
  org: string;
  name: string;
  cloneUrl: string;
  url: string;
  defaultBranch: string;
  branches: string[];
  /** The Git account (connection credential name) that listed this repository. */
  account?: string;
}

// ---------------------------------------------------------------------------
// Session source
// ---------------------------------------------------------------------------

export interface GitSource {
  type: 'git';
  repo: string;
  branch: string;
}

export interface MountMapping {
  host_path: string;
  mount_path: string;
  read_only: boolean;
}

export interface LocalMountSource {
  type: 'local_mount';
  local_path?: string;
  path?: string;
  paths: MountMapping[];
  node_selector?: Record<string, string>;
}

export type SessionSource = GitSource | LocalMountSource;

// ---------------------------------------------------------------------------
// Tracker integration
// ---------------------------------------------------------------------------

export type TrackerIssueStatus = 'backlog' | 'todo' | 'in_progress' | 'done' | 'cancelled';

export interface TrackerIssue {
  id: string;
  identifier: string;
  title: string;
  status: TrackerIssueStatus;
  assignee?: string;
  labels?: string[];
  priority?: number;
  url: string;
}

export interface ProjectRepoMapping {
  linearProjectId: string;
  linearProjectName: string;
  repoUrl: string;
}

// ---------------------------------------------------------------------------
// Session
// ---------------------------------------------------------------------------

export interface ForgeProject {
  id: string;
  name: string;
  slug: string;
  status: 'active' | 'archived';
  instance_id?: string;
  instance_name?: string;
  workspace_path?: string;
  description?: string;
  brief?: string;
  repo_url?: string;
  revision?: number;
}
/**
 * What `PATCH /projects/{id}` accepts besides the revision. Connecting a checkout is a
 * change of `workspace_path`: Forge reads the folder's Git remote and sets `repo_url`.
 */
export interface ForgeProjectChanges {
  name?: string;
  description?: string;
  brief?: string;
  status?: 'active' | 'archived';
  repo_url?: string;
  workspace_path?: string;
}
/** A project role. Roles are a ladder: each one can do everything the ones before it can. */
export type ProjectRole = 'viewer' | 'contributor' | 'approver' | 'owner';
export const PROJECT_ROLES: readonly ProjectRole[] = ['viewer', 'contributor', 'approver', 'owner'];
/** A skill in the A2A `AgentSkill` shape, so people and agents are described alike. */
export interface MemberSkill {
  id: string;
  name: string;
  description?: string;
  tags?: string[];
}
/** A person of the project's org and what they may do on the project. */
export interface ProjectMember {
  project_id: string;
  user_id: string;
  role: ProjectRole;
  expertise: string;
  skills: MemberSkill[];
  display_name: string;
  email: string;
  added_by?: string;
}
/** An active member of the project's org who is not on the project yet. */
export interface ProjectMemberCandidate {
  user_id: string;
  display_name: string;
  email: string;
}
export interface ProjectMemberChange {
  role?: ProjectRole;
  expertise?: string;
  skills?: MemberSkill[];
}
/** How a project owner installs a catalog entry for the whole team. */
export type TeamInstallMethod = 'oauth' | 'client_credentials';

/** One field a catalog entry's config asks for. */
export interface TeamConfigField {
  label?: string;
  type?: string;
  description?: string;
}

/** A catalog entry as the project's owners see it (snake_case, as the API sends it). */
export interface TeamCatalogEntry {
  slug: string;
  name: string;
  description?: string;
  icon: string;
  integration_type: string;
  config_schema: { required?: string[]; properties?: Record<string, TeamConfigField> };
  team_install: TeamInstallMethod;
}

/** An integration the project owns: the team's bot, never a person. */
export interface TeamIntegration {
  id: string;
  slug: string;
  integration_type: string;
  config: Record<string, unknown>;
  enabled: boolean;
  connected_by: string;
  created_at: string;
  updated_at: string;
}

export type TeamRouteEvent = 'needs_decision' | 'person_question' | 'progress' | 'errors';
export type TeamDeliverTo = 'channel' | 'recipient';

/** What one kind of event does with the team's messaging connection. */
export interface TeamRoute {
  event: TeamRouteEvent;
  connection_id: string;
  deliver_to: TeamDeliverTo;
  channel: string;
  enabled: boolean;
}

export type TeamRouteChange = Omit<TeamRoute, 'event'>;

/** One use of a team connection. */
export interface TeamIntegrationUse {
  connection_id: string;
  action: string;
  acting_for: string;
  actor: string;
  subject: string;
  session_id: string;
  occurred_at: string | null;
}

/** A page of uses, newest first; `next_before` is set while older ones remain. */
export interface TeamIntegrationUsagePage {
  items: TeamIntegrationUse[];
  next_before: string | null;
}

export interface TeamServiceAccountConnect {
  slug: string;
  credential: { client_id: string; client_secret: string };
  config: Record<string, string>;
}

export interface SessionProjectMembership {
  sessionId: string;
  revision: number;
  projectId: string | null;
  role?: string;
}
export interface SessionCoordination {
  projectId: string;
  role: string;
  parent?: { instanceId: string; sessionId: string } | null;
  /** What the coordinator asked this worker to do; Forge records it with the membership. */
  objective?: string;
  labels?: string[];
}

/** Where one step of a session's launch stands, as the runtime backend reported it. */
export type StartupStageState = 'pending' | 'active' | 'done' | 'failed';

/** One step of a launch: a pod condition, a kubelet event, a VM's phase, with real times. */
export interface StartupStage {
  key: string;
  label: string;
  state: StartupStageState;
  /** The backend's own words (event message, node, VM phase), when it gave any. */
  detail: string | null;
  /** Null while the stage is still to come. */
  startedAt: string | null;
  finishedAt: string | null;
}

/** The stages of a session's latest launch. */
export interface SessionStartup {
  startedAt: string;
  stages: StartupStage[];
  /** Why Forge could not read the backend's progress on its last attempt. */
  progressError: string | null;
}

/** What a person may do in a room (`niuu.room_access`): chat, also answer gates, everything. */
export type SessionRoomRole = 'viewer' | 'approver' | 'owner';

/** The caller's own standing in a session's room, as Forge reports it for a shared session. */
export interface SessionRoom {
  role: SessionRoomRole;
  participantStatus: string;
  expiresAt: string | null;
}

export interface VolundrSession {
  sessionDefinition?: string;
  /** The Ting workflow run this session works for (the slug of its `workflow:` tag). */
  runId?: string;
  coordination?: SessionCoordination;
  id: string;
  name: string;
  /**
   * The workspace the session was launched from. `null` when Forge withheld it: a person
   * who shares a session without owning it gets a room summary, which never carries the
   * repository, branch or paths. Check `room` first; there is no stand-in source.
   */
  source: SessionSource | null;
  /**
   * The caller's standing in the session's room. Present when Forge answered with a room
   * summary (a shared session, or the owner's own room view); absent on a full session.
   */
  room?: SessionRoom;
  status: SessionStatus;
  model: string;
  personaName?: string;
  lastActive: number;
  messageCount: number;
  tokensUsed: number;
  podName?: string;
  error?: string;
  origin?: SessionOrigin;
  externalSessionId?: string | null;
  hostname?: string;
  chatEndpoint?: string;
  codeEndpoint?: string;
  taskType?: string;
  archivedAt?: Date;
  trackerIssue?: TrackerIssue;
  activityState?:
    | 'provisioning'
    | 'active'
    | 'idle'
    | 'tool_executing'
    | 'awaiting_input'
    | 'stopped'
    | 'error'
    | null;
  activityStateSince?: string | null;
  turnStartedAt?: string | null;
  /** The latest launch's stages; absent for a session not started since Forge records them. */
  startup?: SessionStartup | null;
  /** True when the session is blocked waiting on the user (awaiting_input). */
  needsAttention?: boolean;
  ownerId?: string;
  /** Who owns the session, by name, on a session shared with the caller (when known). */
  ownerName?: string;
  tenantId?: string;
  instanceId?: string;
  instanceName?: string;
  /** The caller's inbox state for the session's latest final reply, when reported. */
  readState?: SessionReadState;
  /** The agent's own one-line summary of what the session is doing, when it wrote one. */
  shortDescription?: string;
  /**
   * Seconds Forge worked on the session in the last 24 hours. Only the session list reports
   * it, and only a host that records turn spans.
   */
  workedSeconds24h?: number;
}

/**
 * True when the caller reaches the session through a room grant (a project membership or a
 * per-session invite) and does not own it. Such a session has no source, code endpoint,
 * files or settings to show; it has its room: chat at `chatEndpoint`, and gates for approvers.
 */
export function isSharedSession(session: Pick<VolundrSession, 'room'>): boolean {
  return session.room !== undefined && session.room.role !== 'owner';
}

/** An invitation to this chat the caller has not answered: they cannot read the session yet. */
export function isInvitation(session: Pick<VolundrSession, 'room'>): boolean {
  return session.room?.participantStatus === 'invited';
}

/** The role a per-session invitation offers, and the room role a project role gives. */
export type ParticipantRole = 'viewer' | 'approver';
export const PARTICIPANT_ROLES: readonly ParticipantRole[] = ['viewer', 'approver'];

/** Where a person's grant on a chat stands. A removed grant is never listed. */
export type ParticipantStatus = 'invited' | 'active';

/** One person invited into a single chat, or already in it. */
export interface SessionParticipant {
  userId: string;
  displayName: string;
  role: ParticipantRole;
  status: ParticipantStatus;
  /** Who invited them (a user id). */
  invitedBy: string;
  expiresAt: string | null;
}

/** A member of the session's org who could be invited into the chat. */
export interface ParticipantCandidate {
  userId: string;
  displayName: string;
  email: string;
}

/**
 * A member of the session's project, with the room role the project gives them on the
 * project's sessions: approver and owner approve, viewer and contributor chat and steer.
 */
export interface SessionProjectPerson {
  userId: string;
  displayName: string;
  projectRole: ProjectRole;
  roomRole: ParticipantRole;
}

/** The room role a project role gives (`room_role_for_project_role`). */
export function roomRoleOfProjectRole(role: ProjectRole): ParticipantRole {
  return role === 'approver' || role === 'owner' ? 'approver' : 'viewer';
}

/** How a shared session reads in a list: "Shared · approver". */
export function sharedSessionLabel(room: SessionRoom): string {
  return `Shared · ${room.role}`;
}

/**
 * Whether the caller may answer a session's gates and permission prompts: `approver` and up in
 * its room, or anyone who reads the full session. The server enforces it; this decides only
 * whether to offer the control.
 */
export function canAnswerGates(session: Pick<VolundrSession, 'room'>): boolean {
  if (session.room === undefined) return true;
  return session.room.role !== 'viewer' && session.room.participantStatus !== 'invited';
}

/**
 * The caller's read state for one session. `latestOutputSeq` and `readThroughSeq` are positions
 * in the session's event log, not counts of replies.
 */
export interface SessionReadState {
  isUnread: boolean;
  latestOutputSeq: number;
  latestOutputTurnId?: string | null;
  latestOutputAt?: string | null;
  readThroughSeq: number;
  manuallyUnread?: boolean;
  revision: number;
}

/** The earlier name of the read state a session response embeds. */
export type SessionInboxReadState = SessionReadState;

/** A read or unread action. `throughSeq` is the output the caller actually saw. */
export interface SessionReadStateChange {
  state: 'read' | 'unread';
  expectedRevision: number;
  throughSeq: number;
}

/** Operator grants that let a session act on other sessions through the Forge MCP. */
export type ForgeMcpGrant = 'message' | 'lifecycle';

/** Forge MCP options for a new session (`forge_mcp` on session create). */
export interface ForgeMcpSessionOptions {
  grants: ForgeMcpGrant[];
}

// ---------------------------------------------------------------------------
// External CLI sessions (Claude Code / Codex discovered on the host)
// ---------------------------------------------------------------------------

export type ExternalSessionHarness = 'claude' | 'codex';

export interface ExternalSession {
  provider: string;
  harness: ExternalSessionHarness;
  externalId: string;
  workspacePath: string;
  title: string;
  model: string;
  createdAt: string | null;
  updatedAt: string | null;
  live: boolean;
  workspaceExists: boolean;
  /** Whether the workspace passes the server's allowed mount prefix policy. */
  workspaceAllowed: boolean;
  /** Volundr session id when the session has already been imported. */
  importedSessionId: string | null;
}

export interface VolundrStats {
  activeSessions: number;
  totalSessions: number;
  sessionsToday: number;
  tokensToday: number;
  localTokens: number;
  cloudTokens: number;
  costToday: number;
  /** 24-point sparkline for each KPI — indexed by key. */
  sparklines?: {
    activePods?: number[];
    tokensToday?: number[];
    costToday?: number[];
    sessionsToday?: number[];
  };
}

export interface VolundrTarget {
  id: string;
  slug: string;
  name: string;
  baseUrl: string;
  config?: Record<string, unknown>;
  enabled: boolean;
  isDefault: boolean;
  visibility?: string;
  tags: string[];
}

// ---------------------------------------------------------------------------
// Messages / Logs / Chronicle
// ---------------------------------------------------------------------------

export type MessageRole = 'user' | 'assistant';

export interface VolundrMessage {
  id: string;
  sessionId: string;
  role: MessageRole;
  content: string;
  timestamp: number;
  tokensIn?: number;
  tokensOut?: number;
  latency?: number;
}

/**
 * What `POST /sessions/{id}/messages` answers. `delivered` means the session's transport
 * accepted the message (200); `pending` means Forge accepted it and is still retrying
 * delivery (202), which is not a confirmed send.
 */
export interface SessionMessageDelivery {
  status: 'delivered' | 'pending';
  requestId: string;
  sessionId: string;
}

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export interface VolundrLog {
  id: string;
  sessionId: string;
  timestamp: number;
  level: LogLevel;
  source: string;
  message: string;
}

export type VolundrLogParticipantKind = 'broker' | 'ravn' | 'service';

export interface VolundrLogParticipant {
  id: string;
  label: string;
  kind: VolundrLogParticipantKind;
}

export interface VolundrAggregatedLog {
  id: string;
  sessionId: string;
  timestamp: number;
  level: LogLevel;
  participant: string;
  participantLabel: string;
  participantKind: VolundrLogParticipantKind;
  source: string;
  message: string;
  sequence: number;
  stream: string;
}

export type ChronicleEventType = 'message' | 'file' | 'git' | 'terminal' | 'error' | 'session';

export interface ChronicleEvent {
  t: number;
  type: ChronicleEventType;
  label: string;
  tokens?: number;
  action?: string;
  ins?: number;
  del?: number;
  hash?: string;
  exit?: number;
}

export interface SessionFile {
  path: string;
  status: 'new' | 'mod' | 'del';
  ins: number;
  del: number;
}

export interface SessionCommit {
  hash: string;
  msg: string;
  time: string;
}

export interface SessionChronicle {
  events: ChronicleEvent[];
  files: SessionFile[];
  commits: SessionCommit[];
  tokenBurn: number[];
}

export interface VolundrSessionTraceSpan {
  id: string;
  sessionId: string;
  traceId: string;
  parentSpanId: string | null;
  kind: string;
  name: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled' | string;
  startedAt: string;
  endedAt: string | null;
  durationMs: number | null;
  actorType?: string | null;
  actorId?: string | null;
  actorLabel?: string | null;
  sourceService?: string | null;
  attributes: Record<string, unknown>;
}

export interface VolundrSessionTraceLane {
  key: string;
  label: string;
  kind: string;
}

export interface VolundrSessionTrace {
  traceId: string;
  sessionId: string;
  startedAt: string | null;
  endedAt: string | null;
  durationMs: number;
  spans: VolundrSessionTraceSpan[];
  lanes: VolundrSessionTraceLane[];
}

export interface VolundrSessionTraceSummary {
  totalDurationMs: number;
  provisioningDurationMs: number;
  setupDurationMs: number;
  workflowDurationMs: number;
  publishDurationMs: number;
  cleanupDurationMs: number;
  activeExecutionDurationMs: number;
  waitingDurationMs: number;
  turnCount: number;
  toolCallCount: number;
  longestSpan: VolundrSessionTraceSpan | null;
}

// ---------------------------------------------------------------------------
// Pull requests / CI
// ---------------------------------------------------------------------------

export type PRStatus = 'open' | 'closed' | 'merged';
export type CIStatusValue = 'pending' | 'running' | 'passed' | 'failed' | 'unknown';

export interface PullRequest {
  number: number;
  title: string;
  url: string;
  repoUrl: string;
  provider: string;
  sourceBranch: string;
  targetBranch: string;
  status: PRStatus;
  description?: string;
  ciStatus?: CIStatusValue;
  reviewStatus?: string;
  createdAt?: string;
  updatedAt?: string;
}

export interface MergeResult {
  merged: boolean;
}

// ---------------------------------------------------------------------------
// MCP servers
// ---------------------------------------------------------------------------

export type McpServerStatus = 'connected' | 'disconnected';

export interface McpServer {
  name: string;
  status: McpServerStatus;
  tools: number;
}

export type McpServerType = 'stdio' | 'sse' | 'http';

export interface McpServerConfig {
  name: string;
  type: McpServerType;
  command?: string;
  url?: string;
  args?: string[];
  env?: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Session definitions
// ---------------------------------------------------------------------------

export interface SessionDefinition {
  key: string;
  displayName: string;
  description: string;
  labels: string[];
  defaultModel: string;
  compatibleProviders: string[];
}

// ---------------------------------------------------------------------------
// Launch specs
// ---------------------------------------------------------------------------

export interface ResourceConfig {
  cpu?: string;
  memory?: string;
  gpu?: string;
  [key: string]: string | undefined;
}

export interface WorkloadConfig {
  [key: string]: unknown;
}

export interface SkillConfig {
  name: string;
  path?: string;
  inline?: string;
}

export interface RuleConfig {
  path?: string;
  inline?: string;
}

export interface TemplateRepo {
  repo: string;
  branch?: string;
  [key: string]: unknown;
}

export interface TerminalSidecarConfig {
  enabled: boolean;
  allowedCommands: string[];
  restricted?: boolean;
}

export type CliTool = 'claude' | 'codex' | 'gemini' | 'aider';

/** Ownership scope of a launch spec. */
export type LaunchScope = 'system' | 'user';

/** Launch specs are the catalog blueprint used to start Forge sessions. */
export interface VolundrLaunchSpec {
  name: string;
  scope: LaunchScope;
  id: string | null;
  description: string;
  isDefault: boolean;
  sessionDefinition: string | null;
  workloadType: string;
  model: string | null;
  systemPrompt: string | null;
  resourceConfig: ResourceConfig;
  mcpServers: McpServerConfig[];
  envVars: Record<string, string>;
  envSecretRefs: string[];
  workloadConfig: WorkloadConfig;
  repos: TemplateRepo[];
  source: SessionSource | null;
  setupScripts: string[];
  workspaceLayout: Record<string, unknown>;
  cliTool: CliTool;
  terminalSidecar: TerminalSidecarConfig;
  skills: SkillConfig[];
  rules: RuleConfig[];
  integrationIds: string[];
  createdAt: string;
  updatedAt: string;
}

// ---------------------------------------------------------------------------
// Credentials and integrations
// ---------------------------------------------------------------------------

export type SecretType =
  'api_key' | 'oauth_token' | 'git_credential' | 'ssh_key' | 'tls_cert' | 'generic';

export interface StoredCredential {
  id: string;
  name: string;
  secretType: SecretType;
  keys: string[];
  scope?: string;
  used?: number;
  metadata: Record<string, string>;
  createdAt: string;
  updatedAt: string;
}

export interface CredentialCreateRequest {
  name: string;
  secretType: SecretType;
  data: Record<string, string>;
  metadata?: Record<string, string>;
}

export interface SecretTypeField {
  key: string;
  label: string;
  type: 'text' | 'password' | 'textarea';
  required: boolean;
}

export interface SecretTypeInfo {
  type: SecretType;
  label: string;
  description: string;
  fields: SecretTypeField[];
  defaultMountType: 'env' | 'file' | 'template';
}

export interface VolundrCredential {
  name: string;
  keys: string[];
}

export interface IntegrationConnection {
  id: string;
  slug?: string;
  integrationType?: string;
  credentialName?: string;
  adapter?: string;
  enabled?: boolean;
  /** Credential lifecycle state: configured, active, auth_required, enrolling, missing… */
  credentialStatus?: string;
  /** Non-secret connection settings, e.g. the Model server's `gateway_url` and `models`. */
  config?: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface IntegrationTestResult {
  success: boolean;
  error?: string;
}

export interface CatalogEntry {
  id: string;
  slug?: string;
  name: string;
  description: string;
  /** ai_provider, source_control, issue_tracker, messaging… */
  integrationType?: string;
  /** Model vendor a connection of this AI provider unlocks (anthropic, openai, xai, deepseek). */
  modelVendor?: string;
}

// ---------------------------------------------------------------------------
// Users / Tenants / Workspaces
// ---------------------------------------------------------------------------

export type VolundrIdentity = AppIdentity;

export interface VolundrUser {
  id: string;
  email: string;
  displayName: string;
  status: string;
  tenantId?: string;
  provisionError?: string;
  createdAt?: string;
}

export interface VolundrTenant {
  id: string;
  path: string;
  name: string;
  parentId?: string;
  tier: string;
  maxSessions: number;
  maxStorageGb: number;
  createdAt?: string;
}

export interface VolundrMember {
  userId: string;
  tenantId: string;
  role: string;
  grantedAt?: string;
}

export interface VolundrProvisioningResult {
  success: boolean;
  userId: string;
  homePvc?: string;
  errors: string[];
}

export type WorkspaceStatus = 'active' | 'archived' | 'deleted';

export interface VolundrWorkspace {
  id: string;
  pvcName: string;
  sessionId: string;
  ownerId: string;
  tenantId: string;
  sizeGb: number;
  status: WorkspaceStatus;
  createdAt: string;
  archivedAt?: string;
  sessionName?: string;
  sourceUrl?: string;
  sourceRef?: string;
}

// ---------------------------------------------------------------------------
// Admin settings
// ---------------------------------------------------------------------------

export interface AdminStorageSettings {
  homeEnabled: boolean;
  fileManagerEnabled: boolean;
}

export interface AdminSettings {
  storage: AdminStorageSettings;
}

// ---------------------------------------------------------------------------
// Personal Access Tokens
// ---------------------------------------------------------------------------

export interface PersonalAccessToken {
  scopes?: string[] | null;
  expires_at?: string | null;
  id: string;
  name: string;
  createdAt: string;
  lastUsedAt: string | null;
}

export interface CreatePATResult {
  id: string;
  name: string;
  token: string;
  createdAt: string;
}

// ---------------------------------------------------------------------------
// Re-export shared SDK types for convenience
// ---------------------------------------------------------------------------

export type { FeatureScope, FeatureModule, UserFeaturePreference };
