/**
 * HTTP adapter for IVolundrService.
 *
 * Accepts any HTTP client with `get` and `post` / `delete` methods —
 * structurally compatible with `createApiClient(baseUrl)` from @niuulabs/query.
 */
import {
  ApiClientError,
  createApiClient,
  getAuthHeaders,
  openEventStream,
  type EventStreamHandle,
  type EventStreamOptions,
} from '@niuulabs/query';
import type {
  IVolundrService,
  PermissionAutoApprovalDecision,
  SessionChangesReadOptions,
  VolundrConversationHistory,
  VolundrSessionChanges,
} from '../ports/IVolundrService';
import type { IFileSystemPort, FileTreeNode } from '../ports/IFileSystemPort';
import type { ForgeEventStreamSource, ForgeStreamListener } from '../ports/IForgeEventStream';
import type {
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
  TrackerIssue,
  ProjectRepoMapping,
  VolundrIdentity,
  VolundrUser,
  VolundrTenant,
  IntegrationConnection,
  IntegrationTestResult,
  StoredCredential,
  CredentialCreateRequest,
  SecretType,
  SecretTypeInfo,
  SecretTypeField,
  SessionDefinition,
  VolundrWorkspace,
  WorkspaceStatus,
  VolundrMember,
  VolundrProvisioningResult,
  VolundrTarget,
  AdminSettings,
  AdminStorageSettings,
  FeatureModule,
  FeatureScope,
  UserFeaturePreference,
  PersonalAccessToken,
  CreatePATResult,
  VolundrSessionTrace,
  VolundrSessionTraceLane,
  VolundrSessionTraceSpan,
  VolundrSessionTraceSummary,
  ExternalSession,
  SessionReadState,
  SessionStartup,
  StartupStageState,
  ForgeProject,
  ParticipantRole,
  ParticipantStatus,
  ProjectRole,
  SessionParticipant,
  SessionRoomRole,
  SessionMembers,
  SessionStatus,
  SessionTeamTaskTo,
  StartTeamRequest,
  StartedTeam,
} from '../models/volundr.model';
import { isSharedSession, roomRoleOfProjectRole } from '../models/volundr.model';
import { normalizeSecretRequest, type SecretRequestPayload } from './secretRequestWire';
import { SessionForkError, SessionReadStateConflictError } from '../domain/session';
import type { SessionRuntimeOffer } from '../domain/sessionRuntimes';
import {
  invalidPayload,
  pairingCodeBody,
  toComputeHostResponse,
  toComputeHostsOverview,
  toNewPairingCode,
  toRemoveResult,
  type ComputeHostPayload,
  type ComputeHostsPayload,
  type NewPairingCodePayload,
} from './computeHostsWire';

/** Minimal HTTP client — structurally compatible with ApiClient from @niuulabs/query. */
export interface HttpClient {
  basePath?: string;
  get<T>(endpoint: string): Promise<T>;
  post<T>(endpoint: string, body?: unknown): Promise<T>;
  delete<T>(endpoint: string, body?: unknown): Promise<T>;
  patch<T>(endpoint: string, body?: unknown): Promise<T>;
  put<T>(endpoint: string, body?: unknown): Promise<T>;
}

type EventStreamOpener = (url: string, options: EventStreamOptions) => EventStreamHandle;
const LIVE_POLL_MS = 2_000;

interface VolundrHttpAdapterOptions {
  niuuBasePath?: string | null;
  /**
   * `guild` (default): the Forge base is the Guild facade and the UI covers every
   * Forge in the registry. `node`: the Forge base is one Forge node; the UI covers
   * only that node, which names its own registry entry at `GET /guild`.
   */
  forgeScope?: ForgeScope;
}

export type ForgeScope = 'guild' | 'node';

interface GuildMembershipPayload {
  guild_url: string;
  instance_id: string;
  instance_name: string;
  instance_slug: string;
}

/** The Forge service plus its shared fleet stream, for features that need raw events. */
export type VolundrHttpAdapter = IVolundrService & ForgeEventStreamSource;

interface FileEntryPayload {
  name: string;
  path: string;
  type: 'file' | 'directory';
  size?: number;
}

interface SessionProjectPayload {
  session_id: string;
  revision: number;
  coordination: { project_id: string; role: string } | null;
}

/** A project's member route, on the host that owns the project. */
function projectMembersPath(
  project: ForgeProject,
  tail = 'members',
  query: Record<string, string> = {},
): string {
  const params = new URLSearchParams(query);
  if (project.instance_id) params.set('instance_id', project.instance_id);
  const search = params.toString();
  return `/projects/${encodeURIComponent(project.id)}/${tail}${search ? `?${search}` : ''}`;
}

type ParticipantPayload = {
  user_id: string;
  display_name?: string;
  role: ParticipantRole;
  status: ParticipantStatus | 'revoked';
  invited_by?: string;
  expires_at?: string | null;
};

function normalizeParticipant(payload: ParticipantPayload): SessionParticipant {
  return {
    userId: payload.user_id,
    displayName: payload.display_name ?? '',
    role: payload.role,
    status: payload.status === 'invited' ? 'invited' : 'active',
    invitedBy: payload.invited_by ?? '',
    expiresAt: payload.expires_at ?? null,
  };
}

type SessionMembersPayload = {
  agents: Array<{
    session_id: string;
    name: string;
    persona_name?: string | null;
    model: string;
    definition?: string | null;
    status: SessionStatus;
    lead: boolean;
  }>;
  people: Array<{
    user_id: string;
    display_name?: string | null;
    role: string;
    status: string;
    online?: boolean | null;
  }>;
};

function normalizeSessionMembers(payload: SessionMembersPayload): SessionMembers {
  return {
    agents: payload.agents.map((agent) => ({
      sessionId: agent.session_id,
      name: agent.name,
      personaName: agent.persona_name ?? '',
      model: agent.model,
      definition: agent.definition ?? null,
      status: agent.status,
      lead: agent.lead === true,
    })),
    people: payload.people.map((person) => ({
      userId: person.user_id,
      displayName: person.display_name ?? '',
      role: person.role,
      status: person.status,
      online: person.online ?? null,
    })),
  };
}

type StartedTeamPayload = {
  session: SessionPayload;
  members: SessionPayload[];
};

type SessionPersonPayload = {
  user_id: string;
  display_name?: string;
  role: ProjectRole;
};

/** A session's chat route (people, invitations), on the host that holds the session. */
function sessionPeoplePath(
  id: string,
  tail: string,
  instanceId?: string,
  query: Record<string, string> = {},
): string {
  const params = new URLSearchParams(query);
  if (instanceId) params.set('instance_id', instanceId);
  const search = params.toString();
  return `/sessions/${encodeURIComponent(id)}/${tail}${search ? `?${search}` : ''}`;
}

function sessionProjectPath(id: string, instanceId?: string): string {
  return `/sessions/${encodeURIComponent(id)}/project${instanceId ? `?instance_id=${encodeURIComponent(instanceId)}` : ''}`;
}

function normalizeSessionProject(payload: SessionProjectPayload) {
  if (!Number.isInteger(payload.revision) || payload.revision < 0) {
    throw new Error(
      'This Forge host does not support versioned project assignment. Update it first.',
    );
  }
  return {
    sessionId: payload.session_id,
    revision: payload.revision,
    projectId: payload.coordination?.project_id ?? null,
    role: payload.coordination?.role,
  };
}

interface FileListPayload {
  entries: FileEntryPayload[];
}

type SessionPayload = {
  /** `room_summary` for a session the caller shares without owning (and the room view). */
  kind?: 'room_summary';
  room?: {
    role: SessionRoomRole;
    participant_status?: string;
    expires_at?: string | null;
  } | null;
  session_definition?: string | null;
  sessionDefinition?: string;
  coordination?: {
    project_id: string;
    role: string;
    parent?: { instance_id: string; session_id: string } | null;
    objective?: string;
    labels?: string[];
  } | null;
  id: string;
  name: string;
  /** Absent on a room summary: Forge withholds the workspace from a participant. */
  source?: VolundrSession['source'];
  status: VolundrSession['status'];
  model: string;
  personaName?: string;
  persona_name?: string;
  lastActive?: number;
  last_active?: string;
  messageCount?: number;
  message_count?: number;
  tokensUsed?: number;
  tokens_used?: number;
  podName?: string;
  pod_name?: string | null;
  error?: string | null;
  origin?: VolundrSession['origin'];
  externalSessionId?: string | null;
  external_session_id?: string | null;
  hostname?: string;
  chatEndpoint?: string | null;
  chat_endpoint?: string | null;
  codeEndpoint?: string | null;
  code_endpoint?: string | null;
  taskType?: string | null;
  task_type?: string | null;
  archivedAt?: Date | string | null;
  archived_at?: string | null;
  trackerIssue?: TrackerIssue;
  trackerIssueId?: string | null;
  tracker_issue_id?: string | null;
  issueTrackerUrl?: string | null;
  issue_tracker_url?: string | null;
  activityState?: VolundrSession['activityState'];
  activity_state?: VolundrSession['activityState'];
  activityStateSince?: string | null;
  activity_state_since?: string | null;
  startup?: StartupPayload | null;
  turnStartedAt?: string | null;
  turn_started_at?: string | null;
  needsAttention?: boolean;
  needs_attention?: boolean;
  activity_metadata?: Record<string, unknown> | null;
  ownerId?: string | null;
  owner_id?: string | null;
  owner_name?: string | null;
  hosted_by?: string | null;
  team?: { task_to?: SessionTeamTaskTo | null } | null;
  team_member?: { lead?: boolean } | null;
  people_count?: number | null;
  tenantId?: string | null;
  tenant_id?: string | null;
  instanceId?: string | null;
  instance_id?: string | null;
  instanceName?: string | null;
  instance_name?: string | null;
  read_state?: ReadStatePayload | null;
  short_description?: string | null;
  worked_seconds_24h?: number | null;
};

type StartupPayload = {
  started_at: string;
  stages?: Array<{
    key: string;
    label: string;
    state: StartupStageState;
    detail?: string | null;
    started_at?: string | null;
    finished_at?: string | null;
  }>;
  progress_error?: string | null;
};

function normalizeStartup(payload: StartupPayload): SessionStartup {
  return {
    startedAt: payload.started_at,
    stages: (payload.stages ?? []).map((stage) => ({
      key: stage.key,
      label: stage.label,
      state: stage.state,
      detail: stage.detail ?? null,
      startedAt: stage.started_at ?? null,
      finishedAt: stage.finished_at ?? null,
    })),
    progressError: payload.progress_error ?? null,
  };
}

type ReadStatePayload = {
  is_unread?: boolean;
  latest_output_seq?: number;
  latest_output_turn_id?: string | null;
  latest_output_at?: string | null;
  read_through_seq?: number;
  manually_unread?: boolean;
  revision?: number;
};

type ExternalSessionPayload = {
  provider: string;
  harness: ExternalSession['harness'];
  external_id: string;
  workspace_path: string;
  title?: string | null;
  model?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  live?: boolean;
  workspace_exists?: boolean;
  workspace_allowed?: boolean;
  imported_session_id?: string | null;
};

type StatsPayload = {
  instance_id?: string;
  instanceId?: string;
  activeSessions?: number;
  active_sessions?: number;
  totalSessions?: number;
  total_sessions?: number;
  sessionsToday?: number;
  sessions_today?: number;
  tokensToday?: number;
  tokens_today?: number;
  localTokens?: number;
  local_tokens?: number;
  cloudTokens?: number;
  cloud_tokens?: number;
  costToday?: number;
  cost_today?: number;
  sparklines?: VolundrStats['sparklines'];
};

type ConversationPayload = {
  turns: Array<{
    id: string;
    role: string;
    content: string;
    parts?: Array<Record<string, unknown>>;
    created_at?: string;
    metadata?: {
      tokens_in?: number;
      tokens_out?: number;
      latency?: number;
    };
    participant_id?: string;
    participant_meta?: Record<string, unknown>;
    thread_id?: string;
    visibility?: 'visible' | 'internal';
    author?: { user_id?: string; display_name?: string } | null;
  }>;
};

type LogPayload = {
  lines: Array<{
    timestamp?: number | string;
    time?: string;
    level?: string;
    logger?: string;
    message: string;
  }>;
};

type AggregatedLogPayload = {
  available_participants?: Array<{
    id: string;
    label?: string;
    kind?: VolundrLogParticipant['kind'];
  }>;
  lines: Array<{
    id?: string;
    timestamp?: number | string;
    level?: string;
    participant?: string;
    participant_label?: string;
    participant_kind?: VolundrLogParticipant['kind'];
    source?: string;
    message?: string;
    sequence?: number;
    stream?: string;
  }>;
};

type ChroniclePayload = {
  events: SessionChronicle['events'];
  files: SessionChronicle['files'];
  commits: SessionChronicle['commits'];
  token_burn?: number[];
  tokenBurn?: number[];
};

type TraceSpanPayload = {
  id: string;
  session_id?: string;
  trace_id?: string;
  parent_span_id?: string | null;
  kind?: string;
  name?: string;
  status?: string;
  started_at?: string | null;
  ended_at?: string | null;
  duration_ms?: number | null;
  actor_type?: string | null;
  actor_id?: string | null;
  actor_label?: string | null;
  source_service?: string | null;
  attributes?: Record<string, unknown>;
};

type TraceLanePayload = {
  key: string;
  label?: string;
  kind?: string;
};

type TracePayload = {
  trace_id?: string;
  session_id?: string;
  started_at?: string | null;
  ended_at?: string | null;
  duration_ms?: number;
  spans?: TraceSpanPayload[];
  lanes?: TraceLanePayload[];
};

type TraceSummaryPayload = {
  total_duration_ms?: number;
  provisioning_duration_ms?: number;
  setup_duration_ms?: number;
  workflow_duration_ms?: number;
  publish_duration_ms?: number;
  cleanup_duration_ms?: number;
  active_execution_duration_ms?: number;
  waiting_duration_ms?: number;
  turn_count?: number;
  tool_call_count?: number;
  longest_span?: TraceSpanPayload | null;
};

type ChronicleEventPayload = {
  session_id: string;
  event: SessionChronicle['events'][number];
  files: SessionChronicle['files'];
  commits: SessionChronicle['commits'];
  token_burn?: number[];
};

type AggregatedLogsResult = {
  lines: VolundrAggregatedLog[];
  participants: VolundrLogParticipant[];
};

type CanonicalCredentialPayload = {
  id: string;
  name: string;
  secret_type?: SecretType;
  secretType?: SecretType;
  keys: string[];
  metadata: Record<string, string>;
  created_at?: string;
  createdAt?: string;
  updated_at?: string;
  updatedAt?: string;
};

type CanonicalCredentialListPayload = {
  credentials: CanonicalCredentialPayload[];
};

type CanonicalSecretTypeFieldPayload = {
  key?: string;
  name?: string;
  label?: string;
  type?: SecretTypeField['type'];
  required?: boolean;
};

type CanonicalSecretTypePayload = {
  type: SecretType;
  label: string;
  description: string;
  fields: CanonicalSecretTypeFieldPayload[];
  default_mount_type?: 'env_file' | 'file' | 'template';
  defaultMountType?: 'env' | 'file' | 'template';
};

type SharedRepoPayload = {
  provider: string;
  org: string;
  name: string;
  url: string;
  clone_url?: string;
  default_branch?: string;
  branches?: string[];
};

type SharedRepoResponse = Record<string, SharedRepoPayload[]>;

type InstanceTargetPayload = {
  config?: Record<string, unknown>;
  id: string;
  slug: string;
  name: string;
  baseUrl?: string;
  base_url?: string;
  enabled: boolean;
  isDefault?: boolean;
  is_default?: boolean;
  visibility?: string;
  tags?: string[];
};

type SessionRuntimePayload = {
  runtime: SessionRuntimeOffer['runtime'];
  grants: SessionRuntimeOffer['grants'];
  readiness: SessionRuntimeOffer['readiness'];
};

type SessionDefinitionPayload = {
  key: string;
  display_name?: string;
  displayName?: string;
  description: string;
  labels: string[];
  default_model?: string;
  defaultModel?: string;
  compatible_providers?: string[];
  compatibleProviders?: string[];
};

type PermissionAutoApprovalPayload = {
  can_auto_approve?: boolean;
  canAutoApprove?: boolean;
  reason?: PermissionAutoApprovalDecision['reason'];
  command?: string | null;
  delay_seconds?: number;
  delaySeconds?: number;
  matched_pattern?: string | null;
  matchedPattern?: string | null;
};

function normalizeSessionDefinition(payload: SessionDefinitionPayload): SessionDefinition {
  return {
    key: payload.key,
    displayName: payload.displayName ?? payload.display_name ?? payload.key,
    description: payload.description,
    labels: payload.labels,
    defaultModel: payload.defaultModel ?? payload.default_model ?? '',
    compatibleProviders: payload.compatibleProviders ?? payload.compatible_providers ?? [],
  };
}

function normalizePermissionAutoApproval(
  payload: PermissionAutoApprovalPayload,
): PermissionAutoApprovalDecision {
  return {
    canAutoApprove: Boolean(payload.canAutoApprove ?? payload.can_auto_approve),
    reason: payload.reason ?? 'endpoint_error',
    command: payload.command ?? null,
    delaySeconds: payload.delaySeconds ?? payload.delay_seconds ?? 5,
    matchedPattern: payload.matchedPattern ?? payload.matched_pattern ?? null,
  };
}

function buildStartSessionBody(
  config: Parameters<IVolundrService['startSession']>[0],
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    name: config.name,
    persona_name: config.personaName,
    source: config.source,
    model: config.model,
    definition: config.definition,
    launch_spec: config.launchSpec,
    launch_spec_id: config.launchSpecId,
    terminal_restricted: Boolean(config.terminalRestricted),
    instance_id: config.instanceId ?? null,
    target_tags: config.targetTags?.length ? config.targetTags : undefined,
    target_match: config.targetMatch ?? undefined,
    workspace_id: config.workspaceId,
    credential_names: config.credentialNames,
    integration_ids: config.integrationIds,
    resource_config: config.resourceConfig,
    system_prompt: config.systemPrompt,
    initial_prompt: config.initialPrompt,
    workload_config: config.workloadConfig ?? {},
    issue_id: config.trackerIssue?.id,
    issue_url: config.trackerIssue?.url,
    // Only a grant the operator asked for is sent; the node's defaults apply regardless.
    forge_mcp: config.forgeMcp?.grants.length ? { grants: config.forgeMcp.grants } : undefined,
  };

  for (const [key, value] of Object.entries(body)) {
    if (value === undefined) delete body[key];
  }
  return body;
}

/** The body of `POST /teams`; a task and who gets it travel together or not at all. */
function buildStartTeamBody(request: StartTeamRequest): Record<string, unknown> {
  const task = request.task?.trim() ?? '';
  const body: Record<string, unknown> = {
    name: request.name,
    source: request.source,
    task,
    task_to: task ? request.taskTo : undefined,
    members: request.members.map((member) => ({
      model: member.model,
      definition: member.definition,
      persona_name: member.personaName ?? '',
      lead: member.lead,
    })),
    invitees: request.invitees,
    launch_spec: request.launchSpec,
    credential_names: request.credentialNames,
    integration_ids: request.integrationIds,
    resource_config: request.resourceConfig,
    terminal_restricted: Boolean(request.terminalRestricted),
    instance_id: request.instanceId ?? null,
    target_tags: request.targetTags?.length ? request.targetTags : undefined,
    target_match: request.targetMatch ?? undefined,
  };
  for (const [key, value] of Object.entries(body)) {
    if (value === undefined) delete body[key];
  }
  return body;
}

function toEpochMs(value?: number | string | null): number {
  if (typeof value === 'number') return value;
  if (typeof value !== 'string') return 0;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}

function toDate(value?: Date | string | null): Date | undefined {
  if (!value) return undefined;
  if (value instanceof Date) return value;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? undefined : new Date(parsed);
}

/** Ting tags each session of a workflow run `workflow:<run slug>`. */
export const WORKFLOW_RUN_TAG = 'workflow:';

/** The workflow run a session works for, from Ting's tag on it. */
function workflowRunOf(session: SessionPayload): string | undefined {
  const identifier = session.trackerIssueId ?? session.tracker_issue_id ?? '';
  return identifier.startsWith(WORKFLOW_RUN_TAG)
    ? identifier.slice(WORKFLOW_RUN_TAG.length)
    : undefined;
}

function normalizeSession(session: SessionPayload): VolundrSession {
  const runId = workflowRunOf(session);
  const trackerIssue =
    session.trackerIssue ??
    (() => {
      const identifier = session.trackerIssueId ?? session.tracker_issue_id ?? null;
      const url = session.issueTrackerUrl ?? session.issue_tracker_url ?? '';
      if (!identifier) return undefined;
      // Ting keys a workflow run's sessions "workflow:<run>": a run, not a ticket to link.
      // The session carries it as `runId`, which links to the run's own page.
      if (runId !== undefined) return undefined;
      return {
        id: identifier,
        identifier,
        title: session.name,
        status: 'todo' as const,
        url,
      };
    })();

  return {
    id: session.id,
    name: session.name,
    ...(runId !== undefined ? { runId } : {}),
    ...(session.coordination
      ? {
          coordination: {
            projectId: session.coordination.project_id,
            role: session.coordination.role,
            parent: session.coordination.parent
              ? {
                  instanceId: session.coordination.parent.instance_id,
                  sessionId: session.coordination.parent.session_id,
                }
              : null,
            ...(session.coordination.objective
              ? { objective: session.coordination.objective }
              : {}),
            labels: session.coordination.labels ?? [],
          },
        }
      : {}),
    source: session.source ?? null,
    ...(session.owner_name ? { ownerName: session.owner_name } : {}),
    ...(session.hosted_by ? { hostedBy: session.hosted_by } : {}),
    ...(session.team ? { team: { taskTo: session.team.task_to ?? null } } : {}),
    ...(session.team_member ? { teamMember: { lead: session.team_member.lead === true } } : {}),
    ...(typeof session.people_count === 'number' ? { peopleCount: session.people_count } : {}),
    ...(session.room
      ? {
          room: {
            role: session.room.role,
            participantStatus: session.room.participant_status ?? 'active',
            expiresAt: session.room.expires_at ?? null,
          },
        }
      : {}),
    status: session.status,
    model: session.model,
    sessionDefinition: session.sessionDefinition ?? session.session_definition ?? undefined,
    personaName: session.personaName ?? session.persona_name ?? undefined,
    lastActive: toEpochMs(session.lastActive ?? session.last_active),
    messageCount: session.messageCount ?? session.message_count ?? 0,
    tokensUsed: session.tokensUsed ?? session.tokens_used ?? 0,
    podName: session.podName ?? session.pod_name ?? undefined,
    error: session.error ?? undefined,
    origin: session.origin,
    externalSessionId: session.externalSessionId ?? session.external_session_id ?? null,
    hostname: session.hostname,
    chatEndpoint: session.chatEndpoint ?? session.chat_endpoint ?? undefined,
    codeEndpoint: session.codeEndpoint ?? session.code_endpoint ?? undefined,
    taskType: session.taskType ?? session.task_type ?? undefined,
    archivedAt: toDate(session.archivedAt ?? session.archived_at),
    trackerIssue,
    activityState: session.activityState ?? session.activity_state ?? undefined,
    activityStateSince: session.activityStateSince ?? session.activity_state_since ?? null,
    turnStartedAt: session.turnStartedAt ?? session.turn_started_at ?? null,
    startup: session.startup ? normalizeStartup(session.startup) : null,
    needsAttention:
      Boolean(session.needsAttention ?? session.needs_attention) ||
      (session.activityState ?? session.activity_state) === 'awaiting_input',
    ownerId: session.ownerId ?? session.owner_id ?? undefined,
    tenantId: session.tenantId ?? session.tenant_id ?? undefined,
    instanceId: session.instanceId ?? session.instance_id ?? undefined,
    instanceName: session.instanceName ?? session.instance_name ?? undefined,
    ...(session.read_state ? { readState: normalizeReadState(session.read_state) } : {}),
    ...(session.short_description ? { shortDescription: session.short_description } : {}),
    ...(typeof session.worked_seconds_24h === 'number'
      ? { workedSeconds24h: session.worked_seconds_24h }
      : {}),
  };
}

/**
 * Normalize a list of sessions one entry at a time. An entry that cannot be read is reported
 * at error level and left out; it must not fail the whole host's list, which the sessions
 * page would show as that Forge being unavailable, hiding every session the person can see.
 */
function normalizeSessionList(
  payloads: readonly SessionPayload[],
  listing: string,
): VolundrSession[] {
  const sessions: VolundrSession[] = [];
  for (const payload of payloads) {
    try {
      sessions.push(normalizeSession(payload));
    } catch (error) {
      console.error(
        `Forge returned a session entry in ${listing} that could not be read; it is not listed.`,
        { id: (payload as { id?: unknown } | null)?.id ?? null, error },
      );
    }
  }
  return sessions;
}

function normalizeReadState(payload: ReadStatePayload): SessionReadState {
  return {
    isUnread: payload.is_unread === true,
    latestOutputSeq: payload.latest_output_seq ?? 0,
    latestOutputTurnId: payload.latest_output_turn_id ?? null,
    latestOutputAt: payload.latest_output_at ?? null,
    readThroughSeq: payload.read_through_seq ?? 0,
    manuallyUnread: payload.manually_unread === true,
    revision: payload.revision ?? 0,
  };
}

function normalizeExternalSession(payload: ExternalSessionPayload): ExternalSession {
  return {
    provider: payload.provider,
    harness: payload.harness,
    externalId: payload.external_id,
    workspacePath: payload.workspace_path,
    title: payload.title ?? '',
    model: payload.model ?? '',
    createdAt: payload.created_at ?? null,
    updatedAt: payload.updated_at ?? null,
    live: Boolean(payload.live),
    workspaceExists: Boolean(payload.workspace_exists),
    workspaceAllowed: payload.workspace_allowed ?? true,
    importedSessionId: payload.imported_session_id ?? null,
  };
}

function normalizeTarget(payload: InstanceTargetPayload): VolundrTarget {
  return {
    id: payload.id,
    slug: payload.slug,
    name: payload.name,
    baseUrl: payload.baseUrl ?? payload.base_url ?? '',
    enabled: payload.enabled,
    isDefault: payload.isDefault ?? payload.is_default ?? false,
    visibility: payload.visibility,
    tags: payload.tags ?? [],
    ...(payload.config ? { config: payload.config } : {}),
  };
}

function normalizeStats(stats: StatsPayload): VolundrStats {
  return {
    activeSessions: stats.activeSessions ?? stats.active_sessions ?? 0,
    totalSessions: stats.totalSessions ?? stats.total_sessions ?? 0,
    sessionsToday: stats.sessionsToday ?? stats.sessions_today ?? 0,
    tokensToday: stats.tokensToday ?? stats.tokens_today ?? 0,
    localTokens: stats.localTokens ?? stats.local_tokens ?? 0,
    cloudTokens: stats.cloudTokens ?? stats.cloud_tokens ?? 0,
    costToday: stats.costToday ?? stats.cost_today ?? 0,
    sparklines: stats.sparklines,
  };
}

interface SessionMessageDeliveryPayload {
  status?: string;
  session_id?: string;
  request_id?: string;
}

/** Forge's 200 (delivered) and 202 (pending) bodies; anything else is not an answer to a send. */
function normalizeMessageDelivery(
  sessionId: string,
  payload: SessionMessageDeliveryPayload | null | undefined,
): SessionMessageDelivery {
  const status = payload?.status;
  if (status !== 'delivered' && status !== 'pending') {
    throw new Error(
      `Forge did not say whether the message to session ${sessionId} was delivered ` +
        `(status ${JSON.stringify(status)}); check the session before sending it again.`,
    );
  }
  if (!payload?.request_id) {
    throw new Error(`Forge accepted the message to session ${sessionId} without a request id.`);
  }
  return { status, requestId: payload.request_id, sessionId: payload.session_id ?? sessionId };
}

function normalizeMessageRole(role: string): VolundrMessage['role'] {
  return role === 'user' ? 'user' : 'assistant';
}

function deriveCanonicalCredentialsBasePath(basePath?: string): string | null {
  if (!basePath) return null;

  const normalized = basePath.replace(/\/$/, '');
  if (normalized.endsWith('/api/v1/credentials')) return normalized;
  if (normalized.endsWith('/api/v1')) return `${normalized}/credentials`;

  const derived = normalized.replace(/\/api\/v1\/(?:forge|volundr)$/, '/api/v1/credentials');
  return derived === normalized ? null : derived;
}

function deriveSharedApiBasePath(basePath?: string): string | null {
  if (!basePath) return null;

  const normalized = basePath.replace(/\/$/, '');
  if (normalized.endsWith('/api/v1')) return normalized;
  if (normalized.endsWith('/api/v1/niuu')) return normalized.replace(/\/niuu$/, '');

  const derived = normalized.replace(/\/api\/v1\/(?:forge|volundr)$/, '/api/v1');
  return derived === normalized ? null : derived;
}

function deriveCanonicalForgeBasePath(basePath?: string): string | null {
  if (!basePath) return null;

  const normalized = basePath.replace(/\/$/, '');
  if (normalized.endsWith('/api/v1/forge')) return normalized;
  if (normalized.endsWith('/api/v1')) return `${normalized}/forge`;
  const sharedBasePath = deriveSharedApiBasePath(normalized);
  return sharedBasePath ? `${sharedBasePath}/forge` : null;
}

/** The `{forge}` base the Forge adapter talks to for a configured service base. */
export function forgeApiBasePath(basePath: string): string {
  return deriveCanonicalForgeBasePath(basePath) ?? basePath;
}

function deriveCanonicalVolundrBasePath(basePath?: string): string | null {
  if (!basePath) return null;

  const normalized = basePath.replace(/\/$/, '');
  if (normalized.endsWith('/api/v1/volundr')) return normalized;
  if (normalized.endsWith('/api/v1')) return `${normalized}/volundr`;
  return null;
}

function deriveNiuuBasePath(basePath?: string): string | null {
  if (!basePath) return null;

  const normalized = basePath.replace(/\/$/, '');
  if (normalized.endsWith('/api/v1/niuu')) return normalized;

  const sharedBasePath = deriveSharedApiBasePath(normalized);
  return sharedBasePath ? `${sharedBasePath}/niuu` : null;
}

function normalizeStoredCredential(
  credential: CanonicalCredentialPayload,
  fallbackSecretType: SecretType = 'generic',
): StoredCredential {
  return {
    id: credential.id,
    name: credential.name,
    secretType: credential.secretType ?? credential.secret_type ?? fallbackSecretType,
    keys: credential.keys,
    metadata: credential.metadata ?? {},
    createdAt: credential.createdAt ?? credential.created_at ?? '',
    updatedAt: credential.updatedAt ?? credential.updated_at ?? '',
  };
}

function normalizeSecretTypeInfo(secretType: CanonicalSecretTypePayload): SecretTypeInfo {
  return {
    type: secretType.type,
    label: secretType.label,
    description: secretType.description,
    fields: (secretType.fields ?? []).map((field) => ({
      key: field.key ?? field.name ?? '',
      label: field.label ?? field.key ?? field.name ?? '',
      type: field.type ?? 'text',
      required: Boolean(field.required),
    })),
    defaultMountType:
      secretType.defaultMountType ??
      (secretType.default_mount_type === 'file' || secretType.default_mount_type === 'template'
        ? secretType.default_mount_type
        : 'env'),
  };
}

function normalizeMessages(sessionId: string, payload: ConversationPayload): VolundrMessage[] {
  return payload.turns.map((turn) => ({
    id: turn.id,
    sessionId,
    role: normalizeMessageRole(turn.role),
    content: turn.content,
    timestamp: toEpochMs(turn.created_at),
    tokensIn: turn.metadata?.tokens_in,
    tokensOut: turn.metadata?.tokens_out,
    latency: turn.metadata?.latency,
  }));
}

function normalizeConversationHistory(payload: ConversationPayload): VolundrConversationHistory {
  return {
    turns: payload.turns.map((turn) => ({
      id: turn.id,
      role: turn.role,
      content: turn.content,
      parts: turn.parts,
      created_at: turn.created_at ?? new Date(0).toISOString(),
      metadata: turn.metadata,
      participant_id: turn.participant_id,
      participant_meta: turn.participant_meta,
      thread_id: turn.thread_id,
      visibility: turn.visibility,
      ...(turn.author ? { author: turn.author } : {}),
    })),
  };
}

function normalizeLogLevel(level?: string): VolundrLog['level'] {
  const normalized = (level ?? 'INFO').toLowerCase();
  if (normalized === 'warning') return 'warn';
  if (normalized === 'debug' || normalized === 'info' || normalized === 'warn') return normalized;
  return 'error';
}

function makeLogFingerprint(
  log: Pick<VolundrLog, 'timestamp' | 'level' | 'source' | 'message'>,
): string {
  return `${log.timestamp}:${log.level}:${log.source}:${log.message}`;
}

function normalizeLogs(sessionId: string, payload: LogPayload): VolundrLog[] {
  const seenCounts = new Map<string, number>();
  return payload.lines.map((line) => {
    const normalized: Omit<VolundrLog, 'id'> = {
      sessionId,
      timestamp: toEpochMs(line.timestamp ?? line.time),
      level: normalizeLogLevel(line.level),
      source: line.logger ?? 'broker',
      message: line.message,
    };
    const fingerprint = makeLogFingerprint(normalized);
    const occurrence = (seenCounts.get(fingerprint) ?? 0) + 1;
    seenCounts.set(fingerprint, occurrence);
    return {
      id: `${sessionId}-log-${fingerprint}:${occurrence}`,
      ...normalized,
    };
  });
}

function normalizeAggregateParticipants(payload: AggregatedLogPayload): VolundrLogParticipant[] {
  return (payload.available_participants ?? []).map((participant) => ({
    id: participant.id,
    label: participant.label ?? participant.id,
    kind: participant.kind ?? 'ravn',
  }));
}

function normalizeAggregatedLogs(
  sessionId: string,
  payload: AggregatedLogPayload,
): { lines: VolundrAggregatedLog[]; participants: VolundrLogParticipant[] } {
  const participants = normalizeAggregateParticipants(payload);
  const seenCounts = new Map<string, number>();
  const lines = payload.lines.map((line, index) => {
    const normalized: Omit<VolundrAggregatedLog, 'id'> = {
      sessionId,
      timestamp: toEpochMs(line.timestamp),
      level: normalizeLogLevel(line.level),
      participant: line.participant ?? 'session',
      participantLabel: line.participant_label ?? line.participant ?? 'session',
      participantKind: line.participant_kind ?? 'ravn',
      source: line.source ?? 'session',
      message: line.message ?? '',
      sequence: line.sequence ?? index,
      stream: line.stream ?? 'workspace',
    };
    const fingerprint = [
      normalized.timestamp,
      normalized.level,
      normalized.participant,
      normalized.source,
      normalized.sequence,
      normalized.message,
    ].join(':');
    const occurrence = (seenCounts.get(fingerprint) ?? 0) + 1;
    seenCounts.set(fingerprint, occurrence);
    return {
      id: line.id ?? `${sessionId}-aggregate-log-${fingerprint}:${occurrence}`,
      ...normalized,
    };
  });
  return { lines, participants };
}

function normalizeChronicle(payload: ChroniclePayload): SessionChronicle {
  return {
    events: payload.events,
    files: payload.files,
    commits: payload.commits,
    tokenBurn: payload.tokenBurn ?? payload.token_burn ?? [],
  };
}

function normalizeTraceSpan(payload: TraceSpanPayload): VolundrSessionTraceSpan {
  return {
    id: payload.id,
    sessionId: payload.session_id ?? '',
    traceId: payload.trace_id ?? '',
    parentSpanId: payload.parent_span_id ?? null,
    kind: payload.kind ?? 'unknown',
    name: payload.name ?? payload.kind ?? 'span',
    status: payload.status ?? 'completed',
    startedAt: payload.started_at ?? new Date(0).toISOString(),
    endedAt: payload.ended_at ?? null,
    durationMs: payload.duration_ms ?? null,
    actorType: payload.actor_type ?? null,
    actorId: payload.actor_id ?? null,
    actorLabel: payload.actor_label ?? null,
    sourceService: payload.source_service ?? null,
    attributes: payload.attributes ?? {},
  };
}

function normalizeTraceLane(payload: TraceLanePayload): VolundrSessionTraceLane {
  return {
    key: payload.key,
    label: payload.label ?? payload.key,
    kind: payload.kind ?? 'system',
  };
}

function normalizeTrace(payload: TracePayload): VolundrSessionTrace {
  return {
    traceId: payload.trace_id ?? '',
    sessionId: payload.session_id ?? '',
    startedAt: payload.started_at ?? null,
    endedAt: payload.ended_at ?? null,
    durationMs: payload.duration_ms ?? 0,
    spans: (payload.spans ?? []).map(normalizeTraceSpan),
    lanes: (payload.lanes ?? []).map(normalizeTraceLane),
  };
}

function normalizeTraceSummary(payload: TraceSummaryPayload): VolundrSessionTraceSummary {
  return {
    totalDurationMs: payload.total_duration_ms ?? 0,
    provisioningDurationMs: payload.provisioning_duration_ms ?? 0,
    setupDurationMs: payload.setup_duration_ms ?? 0,
    workflowDurationMs: payload.workflow_duration_ms ?? 0,
    publishDurationMs: payload.publish_duration_ms ?? 0,
    cleanupDurationMs: payload.cleanup_duration_ms ?? 0,
    activeExecutionDurationMs: payload.active_execution_duration_ms ?? 0,
    waitingDurationMs: payload.waiting_duration_ms ?? 0,
    turnCount: payload.turn_count ?? 0,
    toolCallCount: payload.tool_call_count ?? 0,
    longestSpan: payload.longest_span ? normalizeTraceSpan(payload.longest_span) : null,
  };
}

function normalizeRepo(payload: SharedRepoPayload): VolundrRepo {
  return {
    provider: payload.provider as VolundrRepo['provider'],
    org: payload.org,
    name: payload.name,
    cloneUrl: payload.clone_url ?? `${payload.url}.git`,
    url: payload.url,
    defaultBranch: payload.default_branch ?? 'main',
    branches: payload.branches ?? [],
  };
}

function normalizeRepoList(
  payload: SharedRepoResponse | SharedRepoPayload[] | VolundrRepo[],
): VolundrRepo[] {
  if (Array.isArray(payload)) {
    return payload.map((repo) =>
      'cloneUrl' in repo ? repo : normalizeRepo(repo as SharedRepoPayload),
    );
  }

  // The platform groups repositories by the account that listed them (the
  // connection's credential name); keeping it lets a launch clone with that
  // account's token rather than whichever Git account happens to win.
  return Object.entries(payload).flatMap(([account, repos]) =>
    repos.map((repo) => ({ ...normalizeRepo(repo), account })),
  );
}

type SubscriberSet<T> = Set<(item: T) => void>;

interface PollingConnection<T> {
  subscribers: SubscriberSet<T>;
  knownKeys: string[];
  knownKeySet: Set<string>;
  timer: ReturnType<typeof setInterval> | null;
  loading: boolean;
  hydrated: boolean;
}

function rememberKnownKey<T>(
  connection: PollingConnection<T>,
  key: string,
  maxEntries = 500,
): void {
  if (connection.knownKeySet.has(key)) return;
  connection.knownKeySet.add(key);
  connection.knownKeys.push(key);
  if (connection.knownKeys.length <= maxEntries) return;
  const oldest = connection.knownKeys.shift();
  if (oldest) connection.knownKeySet.delete(oldest);
}

function makeLogKey(log: VolundrLog): string {
  return log.id;
}

function makeAggregatedLogsKey(payload: AggregatedLogsResult): string {
  const ids = payload.lines.map((line) => line.id).join('|');
  const participants = payload.participants.map((participant) => participant.id).join('|');
  return `${participants}::${ids}`;
}

function buildAggregatedLogsQuery(options?: {
  limit?: number;
  level?: string;
  participants?: string[];
  query?: string;
}): string {
  const params = new URLSearchParams();
  if (options?.limit) params.set('lines', String(options.limit));
  if (options?.level) params.set('level', options.level);
  if (options?.participants && options.participants.length > 0) {
    params.set('participants', options.participants.join(','));
  }
  if (options?.query) params.set('query', options.query);
  const encoded = params.toString();
  return encoded ? `?${encoded}` : '';
}

function applyChronicleEvent(
  existing: SessionChronicle | undefined,
  payload: ChronicleEventPayload,
): SessionChronicle {
  const nextEvent = payload.event;
  const existingEvents = existing?.events ?? [];
  const hasEvent = existingEvents.some(
    (event) =>
      event.t === nextEvent.t && event.type === nextEvent.type && event.label === nextEvent.label,
  );

  return {
    events: hasEvent ? existingEvents : [...existingEvents, nextEvent],
    files: payload.files,
    commits: payload.commits,
    tokenBurn: payload.token_burn ?? [],
  };
}

function inferEventType(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null;
  if ('active_sessions' in payload || 'activeSessions' in payload) return 'stats_updated';
  if ('id' in payload && ('status' in payload || 'name' in payload)) return 'session_updated';
  if ('id' in payload) return 'session_deleted';
  return null;
}

function trimTrailingSlash(value: string): string {
  return value.endsWith('/') ? value.slice(0, -1) : value;
}

function trimTrailingSlashes(value: string): string {
  let end = value.length;
  while (end > 0 && value[end - 1] === '/') {
    end -= 1;
  }
  return value.slice(0, end);
}

function trimLeadingSlashes(value: string): string {
  let start = 0;
  while (start < value.length && value[start] === '/') {
    start += 1;
  }
  return value.slice(start);
}

function splitSessionPath(path: string): { root: 'workspace' | 'home'; relativePath: string } {
  const normalized = trimTrailingSlashes(path) || '/workspace';
  if (normalized === '/workspace') return { root: 'workspace', relativePath: '' };
  if (normalized === '/home') return { root: 'home', relativePath: '' };
  if (normalized.startsWith('/workspace/')) {
    return { root: 'workspace', relativePath: normalized.slice('/workspace/'.length) };
  }
  if (normalized.startsWith('/home/')) {
    return { root: 'home', relativePath: normalized.slice('/home/'.length) };
  }
  return { root: 'workspace', relativePath: trimLeadingSlashes(normalized) };
}

function toSessionPath(root: 'workspace' | 'home', relativePath: string): string {
  const prefix = root === 'home' ? '/home' : '/workspace';
  return relativePath ? `${prefix}/${relativePath}` : prefix;
}

function toTreeNode(
  entry: FileEntryPayload,
  root: 'workspace' | 'home',
  children?: FileTreeNode[],
): FileTreeNode {
  return {
    name: entry.name,
    path: toSessionPath(root, entry.path),
    kind: entry.type,
    size: entry.type === 'file' ? entry.size : undefined,
    children,
  };
}

async function readJson<T>(response: Response): Promise<T> {
  return (await response.json()) as T;
}

async function ensureOk(response: Response): Promise<Response> {
  if (response.ok) return response;
  const detail = await response.text();
  throw new Error(detail || `Request failed with ${response.status}`);
}

export const __testables = {
  normalizeSessionDefinition,
  normalizePermissionAutoApproval,
  buildStartSessionBody,
  buildStartTeamBody,
  normalizeSessionMembers,
  toEpochMs,
  toDate,
  normalizeSession,
  normalizeExternalSession,
  normalizeTarget,
  normalizeStats,
  normalizeMessageRole,
  deriveCanonicalCredentialsBasePath,
  deriveSharedApiBasePath,
  deriveCanonicalForgeBasePath,
  deriveCanonicalVolundrBasePath,
  deriveNiuuBasePath,
  normalizeStoredCredential,
  normalizeSecretTypeInfo,
  normalizeMessages,
  normalizeConversationHistory,
  normalizeLogLevel,
  normalizeLogs,
  normalizeAggregateParticipants,
  normalizeAggregatedLogs,
  normalizeChronicle,
  normalizeTraceSpan,
  normalizeTraceLane,
  normalizeTrace,
  normalizeTraceSummary,
  normalizeRepo,
  normalizeRepoList,
  rememberKnownKey,
  buildAggregatedLogsQuery,
  applyChronicleEvent,
  inferEventType,
  trimTrailingSlash,
  trimTrailingSlashes,
  trimLeadingSlashes,
  splitSessionPath,
  toSessionPath,
  toTreeNode,
  ensureOk,
};

export function buildVolundrFileSystemHttpAdapter(options: {
  baseUrl: string;
  fetchImpl?: typeof fetch;
}): IFileSystemPort {
  const fetchImpl = options.fetchImpl ?? fetch;
  const baseUrl = trimTrailingSlash(options.baseUrl);

  function sessionApi(sessionId: string): string {
    return `${baseUrl}/sessions/${encodeURIComponent(sessionId)}`;
  }

  function withAuthHeaders(headers: HeadersInit = {}): Headers {
    return getAuthHeaders(headers);
  }

  async function listDirectory(
    sessionId: string,
    root: 'workspace' | 'home',
    relativePath = '',
  ): Promise<FileTreeNode[]> {
    const params = new URLSearchParams({ root });
    if (relativePath) params.set('path', relativePath);
    const response = await ensureOk(
      await fetchImpl(`${sessionApi(sessionId)}/files?${params.toString()}`, {
        headers: withAuthHeaders(),
      }),
    );
    const payload = await readJson<FileListPayload>(response);
    return payload.entries.map((entry) => toTreeNode(entry, root));
  }

  return {
    async listTree(sessionId: string): Promise<FileTreeNode[]> {
      const nodes = await listDirectory(sessionId, 'workspace');
      const withChildren = await Promise.all(
        nodes.map(async (node) => {
          if (node.kind !== 'directory') return node;
          const { root, relativePath } = splitSessionPath(node.path);
          const children = await listDirectory(sessionId, root, relativePath);
          return { ...node, children };
        }),
      );
      return withChildren;
    },

    async expandDirectory(sessionId: string, path: string): Promise<FileTreeNode[]> {
      const { root, relativePath } = splitSessionPath(path);
      return listDirectory(sessionId, root, relativePath);
    },

    async readFile(sessionId: string, path: string): Promise<string> {
      const { root, relativePath } = splitSessionPath(path);
      const params = new URLSearchParams({ root, path: relativePath });
      const response = await ensureOk(
        await fetchImpl(`${sessionApi(sessionId)}/files/download?${params.toString()}`, {
          headers: withAuthHeaders(),
        }),
      );
      return response.text();
    },

    async downloadFile(sessionId: string, path: string, signal?: AbortSignal): Promise<Blob> {
      const { root, relativePath } = splitSessionPath(path);
      const params = new URLSearchParams({ root, path: relativePath });
      const response = await ensureOk(
        await fetchImpl(`${sessionApi(sessionId)}/files/download?${params}`, {
          headers: withAuthHeaders(),
          signal,
        }),
      );
      return response.blob();
    },

    async downloadPresentedFile(
      sessionId: string,
      fileId: string,
      signal?: AbortSignal,
    ): Promise<Blob> {
      const response = await ensureOk(
        await fetchImpl(`${sessionApi(sessionId)}/files/presented/${encodeURIComponent(fileId)}`, {
          headers: withAuthHeaders(),
          signal,
        }),
      );
      return response.blob();
    },

    async writeFile(sessionId: string, path: string, content: string): Promise<void> {
      const { root, relativePath } = splitSessionPath(path);
      const segments = relativePath.split('/').filter(Boolean);
      const fileName = segments.pop() ?? 'untitled';
      const parentPath = segments.join('/');

      if (parentPath) {
        const mkdirResponse = await fetchImpl(`${sessionApi(sessionId)}/files/mkdir`, {
          method: 'POST',
          headers: withAuthHeaders({ 'content-type': 'application/json' }),
          body: JSON.stringify({ path: parentPath, root }),
        });
        if (!mkdirResponse.ok && mkdirResponse.status !== 409) {
          await ensureOk(mkdirResponse);
        }
      }

      const form = new FormData();
      form.append('files', new Blob([content], { type: 'text/plain' }), fileName);
      const params = new URLSearchParams({ root });
      if (parentPath) params.set('path', parentPath);
      await ensureOk(
        await fetchImpl(`${sessionApi(sessionId)}/files/upload?${params.toString()}`, {
          method: 'POST',
          headers: withAuthHeaders(),
          body: form,
        }),
      );
    },

    async deletePaths(sessionId: string, paths: string[]): Promise<void> {
      for (const path of paths) {
        const { root, relativePath } = splitSessionPath(path);
        const params = new URLSearchParams({ root, path: relativePath });
        await ensureOk(
          await fetchImpl(`${sessionApi(sessionId)}/files?${params.toString()}`, {
            method: 'DELETE',
            headers: withAuthHeaders(),
          }),
        );
      }
    },
  };
}

/** Every compute route names the Forge it is for: the Guild facade proxies by `?instance_id=`. */
function computeHostsPath(instanceId: string, suffix = ''): string {
  return `/compute/hosts${suffix}?instance_id=${encodeURIComponent(instanceId)}`;
}

/** A Forge without `compute.hosts` answers 404 on the list: that is "no Hosts view", not a failure. */
function isNotFound(error: unknown): boolean {
  return error instanceof ApiClientError && error.status === 404;
}

type ComputeHostMethods = Pick<
  IVolundrService,
  | 'getComputeHosts'
  | 'getComputeHostTagSets'
  | 'createPairingCode'
  | 'revokePairingCode'
  | 'updateComputeHost'
  | 'drainComputeHost'
  | 'resumeComputeHost'
  | 'reprepareComputeHost'
  | 'checkComputeHost'
  | 'removeComputeHost'
>;

function computeHostMethods(client: HttpClient): ComputeHostMethods {
  const hostPath = (instanceId: string, hostId: string, action = '') =>
    computeHostsPath(instanceId, `/${encodeURIComponent(hostId)}${action}`);
  return {
    getComputeHosts: async (instanceId) => {
      try {
        return toComputeHostsOverview(
          await client.get<ComputeHostsPayload>(computeHostsPath(instanceId)),
          instanceId,
        );
      } catch (error) {
        if (isNotFound(error)) return null;
        throw error;
      }
    },
    getComputeHostTagSets: async (instanceId) => {
      let payload: { tag_sets?: string[][] };
      try {
        payload = await client.get<{ tag_sets?: string[][] }>(
          computeHostsPath(instanceId, '/tag-sets'),
        );
      } catch (error) {
        if (isNotFound(error)) return null;
        throw error;
      }
      if (!Array.isArray(payload?.tag_sets)) throw invalidPayload('host tag sets', instanceId);
      return payload.tag_sets;
    },
    createPairingCode: async (instanceId, request) =>
      toNewPairingCode(
        await client.post<NewPairingCodePayload>(
          computeHostsPath(instanceId, '/pairing-codes'),
          pairingCodeBody(request),
        ),
        instanceId,
      ),
    revokePairingCode: async (instanceId, codeId) => {
      await client.delete<void>(
        computeHostsPath(instanceId, `/pairing-codes/${encodeURIComponent(codeId)}`),
      );
    },
    updateComputeHost: async (instanceId, hostId, changes) =>
      toComputeHostResponse(
        await client.patch<ComputeHostPayload>(hostPath(instanceId, hostId), changes),
        instanceId,
      ),
    drainComputeHost: async (instanceId, hostId, remove) =>
      toComputeHostResponse(
        await client.post<ComputeHostPayload>(hostPath(instanceId, hostId, '/drain'), { remove }),
        instanceId,
      ),
    resumeComputeHost: async (instanceId, hostId) =>
      toComputeHostResponse(
        await client.post<ComputeHostPayload>(hostPath(instanceId, hostId, '/resume')),
        instanceId,
      ),
    reprepareComputeHost: async (instanceId, hostId) =>
      toComputeHostResponse(
        await client.post<ComputeHostPayload>(hostPath(instanceId, hostId, '/reprepare')),
        instanceId,
      ),
    checkComputeHost: async (instanceId, hostId) =>
      toComputeHostResponse(
        await client.post<ComputeHostPayload>(hostPath(instanceId, hostId, '/check')),
        instanceId,
      ),
    removeComputeHost: async (instanceId, hostId) =>
      toRemoveResult(
        await client.delete<{ removed: boolean; host_cleanup_command?: string | null }>(
          hostPath(instanceId, hostId),
        ),
        instanceId,
      ),
  };
}

export function buildVolundrHttpAdapter(
  client: HttpClient,
  openStream: EventStreamOpener = openEventStream,
  options: VolundrHttpAdapterOptions = {},
): VolundrHttpAdapter {
  const forgeClient = (() => {
    const forgeBasePath = deriveCanonicalForgeBasePath(client.basePath);
    return forgeBasePath && forgeBasePath !== client.basePath
      ? createApiClient(forgeBasePath)
      : client;
  })();
  const catalogClient = (() => {
    const volundrBasePath = deriveCanonicalVolundrBasePath(client.basePath);
    return volundrBasePath && volundrBasePath !== client.basePath
      ? createApiClient(volundrBasePath)
      : client;
  })();
  const credentialsClient = (() => {
    const credentialsBasePath = deriveCanonicalCredentialsBasePath(client.basePath);
    return credentialsBasePath ? createApiClient(credentialsBasePath) : client;
  })();
  const sharedClient = (() => {
    const sharedBasePath = deriveSharedApiBasePath(client.basePath);
    return sharedBasePath ? createApiClient(sharedBasePath) : client;
  })();
  const niuuClient = (() => {
    const niuuBasePath = options.niuuBasePath ?? deriveNiuuBasePath(client.basePath);
    return niuuBasePath ? createApiClient(niuuBasePath) : null;
  })();
  const trackerClient = sharedClient;

  const sessionSubscribers = new Set<(sessions: VolundrSession[]) => void>();
  const statsSubscribers = new Set<(stats: VolundrStats) => void>();
  const chronicleSubscribers = new Map<string, Set<(chronicle: SessionChronicle) => void>>();
  const messageSubscribers = new Map<string, PollingConnection<VolundrMessage>>();
  const logSubscribers = new Map<string, PollingConnection<VolundrLog>>();
  const sessionCache = new Map<string, VolundrSession>();
  const archivedSessionOwners = new Map<string, string>();
  const chronicleCache = new Map<string, SessionChronicle>();
  let statsCache: VolundrStats | null = null;
  let streamHandle: EventStreamHandle | null = null;
  const streamListeners = new Set<ForgeStreamListener>();
  let streamStatus: 'open' | 'error' | null = null;
  const readStateRefreshes = new Set<string>();
  let sessionsHydration: Promise<void> | null = null;
  let statsHydration: Promise<void> | null = null;
  const chronicleHydration = new Map<string, Promise<void>>();

  function publishSessions(): void {
    const snapshot = Array.from(sessionCache.values());
    for (const subscriber of sessionSubscribers) subscriber(snapshot);
  }

  function publishStats(): void {
    if (!statsCache) return;
    for (const subscriber of statsSubscribers) subscriber(statsCache);
  }

  function publishChronicle(sessionId: string): void {
    const chronicle = chronicleCache.get(sessionId);
    if (!chronicle) return;
    for (const subscriber of chronicleSubscribers.get(sessionId) ?? []) subscriber(chronicle);
  }

  function keepNewerActivity(fresh: VolundrSession): VolundrSession {
    const prior = sessionCache.get(fresh.id);
    if (
      !prior ||
      prior.instanceId !== fresh.instanceId ||
      prior.status !== 'running' ||
      fresh.status !== 'running'
    )
      return fresh;
    const previous = Date.parse(prior.activityStateSince ?? '');
    const next = Date.parse(fresh.activityStateSince ?? '');
    if (!Number.isFinite(previous) || (Number.isFinite(next) && next >= previous)) return fresh;
    return {
      ...fresh,
      activityState: prior.activityState,
      activityStateSince: prior.activityStateSince,
      turnStartedAt: prior.turnStartedAt,
      needsAttention: prior.needsAttention,
    };
  }

  /**
   * A stream event is broadcast, not read for this caller: it carries neither the caller's read
   * state nor the 24-hour work total the list adds. Keep what the cache already knows.
   */
  function keepListOnlyFields(fresh: VolundrSession): VolundrSession {
    const prior = sessionCache.get(fresh.id);
    if (!prior || prior.instanceId !== fresh.instanceId) return fresh;
    const readState = fresh.readState ?? prior.readState;
    const workedSeconds24h = fresh.workedSeconds24h ?? prior.workedSeconds24h;
    return {
      ...fresh,
      ...(readState ? { readState } : {}),
      ...(workedSeconds24h !== undefined ? { workedSeconds24h } : {}),
    };
  }

  function updateSessionCache(sessions: VolundrSession[]): void {
    sessionCache.clear();
    for (const session of sessions) sessionCache.set(session.id, session);
  }

  function readSessionList(endpoint: string, signal?: AbortSignal) {
    return signal
      ? forgeClient.get<SessionPayload[]>(endpoint, { signal })
      : forgeClient.get<SessionPayload[]>(endpoint);
  }

  async function loadSessions(
    endpoint: string,
    options?: { instanceId?: string; signal?: AbortSignal },
  ): Promise<VolundrSession[]> {
    const scoped = options?.instanceId
      ? `${endpoint}${endpoint.includes('?') ? '&' : '?'}instance_id=${encodeURIComponent(options.instanceId)}`
      : endpoint;
    const sessions = normalizeSessionList(
      await readSessionList(scoped, options?.signal),
      scoped,
    ).map(keepNewerActivity);
    if (options?.instanceId) {
      for (const [id, session] of sessionCache) {
        if (session.instanceId === options.instanceId) sessionCache.delete(id);
      }
      for (const session of sessions) sessionCache.set(session.id, session);
    } else updateSessionCache(sessions);
    publishSessions();
    return sessions;
  }

  async function loadSessionChanges(
    cursor: string,
    { instanceId, archived, signal }: SessionChangesReadOptions,
  ): Promise<VolundrSessionChanges> {
    const query = new URLSearchParams({ changed_since: cursor, instance_id: instanceId });
    if (archived) query.set('status', 'archived');
    const endpoint = `/sessions?${query}`;
    const body = await (signal
      ? forgeClient.get<VolundrSessionChanges<SessionPayload> | SessionPayload[]>(endpoint, {
          signal,
        })
      : forgeClient.get<VolundrSessionChanges<SessionPayload> | SessionPayload[]>(endpoint));
    // A gateway from before changed_since answers with the whole inventory: a
    // complete answer, and cursor "0" asks for everything again next time.
    const payload = Array.isArray(body)
      ? { cursor: '0', ids: body.map((session) => session.id), sessions: body }
      : body;
    if (
      typeof payload?.cursor !== 'string' ||
      !Array.isArray(payload.ids) ||
      !Array.isArray(payload.sessions)
    ) {
      throw new Error(`Invalid session changes from ${instanceId}`);
    }
    const listed = new Set(payload.ids);
    const sessions = normalizeSessionList(payload.sessions, endpoint);
    // Mirror loadSessions/listArchivedSessions: this host's cached membership is
    // exactly what it lists now.
    if (archived) {
      for (const [id, owner] of archivedSessionOwners) {
        if (owner === instanceId && !listed.has(id)) archivedSessionOwners.delete(id);
      }
      for (const session of sessions) archivedSessionOwners.set(session.id, instanceId);
      return { cursor: payload.cursor, ids: payload.ids, sessions };
    }
    const live = sessions.map(keepNewerActivity);
    for (const [id, session] of sessionCache) {
      if (session.instanceId === instanceId && !listed.has(id)) sessionCache.delete(id);
    }
    for (const session of live) sessionCache.set(session.id, session);
    publishSessions();
    return { cursor: payload.cursor, ids: payload.ids, sessions: live };
  }

  /** The host a session was listed from, when this client has seen it. */
  function cachedInstanceId(sessionId: string): string | undefined {
    return sessionCache.get(sessionId)?.instanceId ?? archivedSessionOwners.get(sessionId);
  }

  function sessionPath(sessionId: string, suffix = '', explicitInstanceId?: string): string {
    const instanceId =
      explicitInstanceId ??
      sessionCache.get(sessionId)?.instanceId ??
      archivedSessionOwners.get(sessionId);
    const path = `/sessions/${sessionId}${suffix}`;
    if (!instanceId) return path;
    return `${path}${path.includes('?') ? '&' : '?'}instance_id=${encodeURIComponent(instanceId)}`;
  }

  function secretRequestPath(sessionId: string, requestId: string, action = ''): string {
    return sessionPath(sessionId, `/secret-requests/${encodeURIComponent(requestId)}${action}`);
  }

  /**
   * One session as the caller may read it. The owner, an admin and a tenant viewer read the
   * full session. A person who shares the session (a project member or an invitee) is refused
   * it ("read") and reads its room view instead ("read_room"): the same entry the list gave
   * them, without the workspace. The list says which kind a session is, so a known shared
   * session goes straight to the room view; otherwise the full read comes first, and a refusal
   * (403, which the Guild facade passes on from the node that holds the session) is answered by
   * the room view.
   */
  async function readSessionPayload(
    id: string,
    options?: { instanceId?: string; signal?: AbortSignal },
  ): Promise<SessionPayload | null> {
    const read = (path: string) =>
      options?.signal
        ? forgeClient.get<SessionPayload | null>(path, { signal: options.signal })
        : forgeClient.get<SessionPayload | null>(path);
    const roomPath = sessionPath(id, '/room', options?.instanceId);
    const cached = sessionCache.get(id);
    if (cached && isSharedSession(cached)) return read(roomPath);
    try {
      const full = await read(sessionPath(id, '', options?.instanceId));
      if (full) return full;
    } catch (error) {
      if (!(error instanceof ApiClientError) || error.status !== 403) throw error;
    }
    return read(roomPath);
  }

  async function loadSession(
    id: string,
    options?: { instanceId?: string; signal?: AbortSignal },
  ): Promise<VolundrSession | null> {
    const session = await readSessionPayload(id, options);
    if (!session) {
      sessionCache.delete(id);
      publishSessions();
      return null;
    }
    const fetched = normalizeSession(session);
    if (options?.instanceId && fetched.instanceId && fetched.instanceId !== options.instanceId) {
      throw new Error(
        `Session ${id} was returned by ${fetched.instanceId}, expected ${options.instanceId}`,
      );
    }
    if (options?.instanceId && !fetched.instanceId) {
      fetched.instanceId = options.instanceId;
    }
    const normalized = keepNewerActivity(fetched);
    sessionCache.set(normalized.id, normalized);
    publishSessions();
    return normalized;
  }

  async function loadStats(signal?: AbortSignal): Promise<VolundrStats> {
    statsCache = normalizeStats(
      await (signal
        ? forgeClient.get<StatsPayload>('/stats', { signal })
        : forgeClient.get<StatsPayload>('/stats')),
    );
    publishStats();
    return statsCache;
  }

  async function loadChronicle(sessionId: string): Promise<SessionChronicle | null> {
    const payload = await forgeClient.get<ChroniclePayload | null>(
      `/chronicles/${sessionId}/timeline`,
    );
    if (!payload) {
      chronicleCache.delete(sessionId);
      return null;
    }
    const chronicle = normalizeChronicle(payload);
    chronicleCache.set(sessionId, chronicle);
    publishChronicle(sessionId);
    return chronicle;
  }

  async function loadMessages(sessionId: string): Promise<VolundrMessage[]> {
    return forgeClient
      .get<ConversationPayload>(sessionPath(sessionId, '/conversation'))
      .then((payload) => normalizeMessages(sessionId, payload));
  }

  async function loadLogs(sessionId: string, limit?: number): Promise<VolundrLog[]> {
    return forgeClient
      .get<LogPayload>(sessionPath(sessionId, `/logs${limit ? `?lines=${limit}` : ''}`))
      .then((payload) => normalizeLogs(sessionId, payload));
  }

  async function loadAggregatedLogs(
    sessionId: string,
    options?: {
      limit?: number;
      level?: string;
      participants?: string[];
      query?: string;
    },
  ): Promise<AggregatedLogsResult> {
    return forgeClient
      .get<AggregatedLogPayload>(
        sessionPath(sessionId, `/logs/aggregate${buildAggregatedLogsQuery(options)}`),
      )
      .then((payload) => normalizeAggregatedLogs(sessionId, payload));
  }

  function ensurePollingConnection<T>(
    registry: Map<string, PollingConnection<T>>,
    sessionId: string,
    fetchItems: () => Promise<T[]>,
    keyOf: (item: T) => string,
  ): PollingConnection<T> {
    const existing = registry.get(sessionId);
    if (existing) return existing;

    const connection: PollingConnection<T> = {
      subscribers: new Set(),
      knownKeys: [],
      knownKeySet: new Set(),
      timer: null,
      loading: false,
      hydrated: false,
    };

    const poll = async (): Promise<void> => {
      if (connection.loading) return;
      connection.loading = true;
      try {
        const items = await fetchItems();
        if (!connection.hydrated) {
          for (const item of items) rememberKnownKey(connection, keyOf(item));
          connection.hydrated = true;
          return;
        }
        for (const item of items) {
          const key = keyOf(item);
          if (connection.knownKeySet.has(key)) continue;
          rememberKnownKey(connection, key);
          for (const subscriber of connection.subscribers) subscriber(item);
        }
      } catch {
        // Best-effort live polling on top of stable snapshot endpoints.
      } finally {
        connection.loading = false;
      }
    };

    void poll();
    connection.timer = setInterval(() => {
      void poll();
    }, LIVE_POLL_MS);
    registry.set(sessionId, connection);
    return connection;
  }

  function maybeClosePollingConnection<T>(
    registry: Map<string, PollingConnection<T>>,
    sessionId: string,
  ): void {
    const connection = registry.get(sessionId);
    if (!connection || connection.subscribers.size > 0) return;
    if (connection.timer) clearInterval(connection.timer);
    registry.delete(sessionId);
  }

  function publishStreamStatus(status: 'open' | 'error'): void {
    streamStatus = status;
    for (const listener of Array.from(streamListeners)) listener.onStatus?.(status);
  }

  function publishStreamEvent(type: string, data: unknown): void {
    for (const listener of Array.from(streamListeners)) {
      try {
        listener.onEvent({ type, data });
      } catch {
        // One feature's listener must never break the shared fleet stream.
      }
    }
  }

  /** The read-state hint carries no state; re-read the authorized session projection. */
  function refreshReadState(sessionId: string): void {
    if (!sessionCache.has(sessionId) || readStateRefreshes.has(sessionId)) return;
    readStateRefreshes.add(sessionId);
    void loadSession(sessionId)
      .catch(() => undefined)
      .finally(() => {
        readStateRefreshes.delete(sessionId);
      });
  }

  function ensureStream(): void {
    if (streamHandle || !forgeClient.basePath) return;
    streamHandle = openStream(`${forgeClient.basePath}/sessions/stream?all_instances=true`, {
      onMessage: () => {},
      onOpen: () => publishStreamStatus('open'),
      onError: () => publishStreamStatus('error'),
      onEvent: ({ event, data }) => {
        try {
          const payload = JSON.parse(data) as SessionPayload | StatsPayload | { id?: string };
          const eventType = event ?? inferEventType(payload);
          if (eventType) publishStreamEvent(eventType, payload);
          // Notifications are owned by the notifications feed's listener.
          if (eventType === 'session_notification') return;
          if (eventType === 'session_read_state') {
            const sessionId = (payload as { session_id?: unknown }).session_id;
            if (typeof sessionId === 'string') refreshReadState(sessionId);
            return;
          }
          if (eventType === 'session_created' || eventType === 'session_updated') {
            const session = keepListOnlyFields(
              keepNewerActivity(normalizeSession(payload as SessionPayload)),
            );
            sessionCache.set(session.id, session);
            publishSessions();
            return;
          }
          if (eventType === 'session_deleted') {
            const sessionId =
              typeof payload === 'object' && payload && 'id' in payload ? payload.id : null;
            if (typeof sessionId === 'string') {
              sessionCache.delete(sessionId);
              publishSessions();
            }
            return;
          }
          if (eventType === 'session_activity' || eventType === 'session_needs_input') {
            // Live activity transitions (active/idle/tool_executing/awaiting_input)
            // ride their own event, not session_updated — apply them to the cached
            // session so a "needs you" badge flips immediately, not on the next poll.
            const activity = payload as {
              session_id?: string;
              state?: VolundrSession['activityState'];
              instance_id?: string;
              activity_state_since?: string | null;
              turn_started_at?: string | null;
            };
            const sessionId = activity.session_id;
            if (typeof sessionId !== 'string') return;
            const existing = sessionCache.get(sessionId);
            if (
              !existing ||
              existing.status !== 'running' ||
              (activity.instance_id && activity.instance_id !== existing.instanceId)
            )
              return;
            const previous = Date.parse(existing.activityStateSince ?? '');
            const next = Date.parse(activity.activity_state_since ?? '');
            if (Number.isFinite(previous) && (!Number.isFinite(next) || next < previous)) return;
            const state =
              eventType === 'session_needs_input'
                ? 'awaiting_input'
                : (activity.state ?? existing.activityState);
            sessionCache.set(sessionId, {
              ...existing,
              activityState: state,
              activityStateSince: activity.activity_state_since ?? null,
              turnStartedAt: ['idle', 'stopped', 'error'].includes(state ?? '')
                ? null
                : 'turn_started_at' in activity
                  ? activity.turn_started_at
                  : existing.turnStartedAt,
              needsAttention: state === 'awaiting_input',
            });
            publishSessions();
            return;
          }
          if (eventType === 'stats_updated') {
            // A per-host counter from the fleet feed is not an aggregate snapshot.
            if ('instance_id' in payload) return;
            statsCache = normalizeStats(payload as StatsPayload);
            publishStats();
            return;
          }
          if (eventType === 'chronicle_event') {
            const chronicleEvent = payload as ChronicleEventPayload;
            const sessionId = chronicleEvent.session_id;
            if (typeof sessionId !== 'string') return;
            chronicleCache.set(
              sessionId,
              applyChronicleEvent(chronicleCache.get(sessionId), chronicleEvent),
            );
            publishChronicle(sessionId);
          }
        } catch {
          // Drop malformed frames.
        }
      },
    });
  }

  function maybeCloseStream(): void {
    const hasChronicleSubscribers = Array.from(chronicleSubscribers.values()).some(
      (subscribers) => subscribers.size > 0,
    );
    if (
      sessionSubscribers.size > 0 ||
      statsSubscribers.size > 0 ||
      streamListeners.size > 0 ||
      hasChronicleSubscribers
    )
      return;
    streamHandle?.close();
    streamHandle = null;
    streamStatus = null;
  }

  function hydrateSessions(): void {
    if (sessionCache.size > 0 || sessionsHydration) return;
    sessionsHydration = loadSessions('/sessions')
      .then(() => undefined)
      .catch(() => undefined)
      .finally(() => {
        sessionsHydration = null;
      });
  }

  function hydrateStats(): void {
    if (statsCache || statsHydration) return;
    statsHydration = loadStats()
      .then(() => undefined)
      .catch(() => undefined)
      .finally(() => {
        statsHydration = null;
      });
  }

  function hydrateChronicle(sessionId: string): void {
    if (chronicleCache.has(sessionId) || chronicleHydration.has(sessionId)) return;
    chronicleHydration.set(
      sessionId,
      loadChronicle(sessionId)
        .then(() => undefined)
        .catch(() => undefined)
        .finally(() => {
          chronicleHydration.delete(sessionId);
        }),
    );
  }

  return {
    getFeatures: async (instanceId) => {
      const flags = await forgeClient.get<{
        local_mounts_enabled: boolean;
        file_manager_enabled: boolean;
        mini_mode: boolean;
      }>(`/feature-flags${instanceId ? `?instance_id=${encodeURIComponent(instanceId)}` : ''}`);
      return {
        localMountsEnabled: flags.local_mounts_enabled,
        fileManagerEnabled: flags.file_manager_enabled,
        miniMode: flags.mini_mode,
      };
    },
    getLivePreviewConfig: async (instanceId) => {
      const payload = await forgeClient.get<{ enabled: boolean; origin_template: string }>(
        `/settings/live-preview${instanceId ? `?instance_id=${encodeURIComponent(instanceId)}` : ''}`,
      );
      return { enabled: payload.enabled, originTemplate: payload.origin_template };
    },
    getSessionRuntimes: async (instanceId) => {
      const payload = await forgeClient.get<{ runtimes?: SessionRuntimePayload[] }>(
        `/session-runtimes${instanceId ? `?instance_id=${encodeURIComponent(instanceId)}` : ''}`,
      );
      if (!Array.isArray(payload?.runtimes)) {
        throw new Error(`Invalid session runtimes from ${instanceId ?? 'the default host'}`);
      }
      if (payload.runtimes.length > 1) {
        throw new Error(
          `${instanceId ?? 'The default host'} offered ${payload.runtimes.length} session ` +
            'runtimes; a host runs exactly one',
        );
      }
      return payload.runtimes.map((offer) => ({
        runtime: offer.runtime,
        grants: offer.grants,
        readiness: offer.readiness,
      }));
    },
    ...computeHostMethods(forgeClient),
    getSessionDefinitions: async () => {
      const payload = await catalogClient.get<SessionDefinitionPayload[]>('/session-definitions');
      return payload.map(normalizeSessionDefinition);
    },
    getSessions: (options) => loadSessions('/sessions', options),
    getSession: (id, options) => loadSession(id, options),
    getSessionChanges: loadSessionChanges,
    getRuntimeVersion: (id, instanceId) => {
      const owner = instanceId ?? sessionCache.get(id)?.instanceId ?? archivedSessionOwners.get(id);
      const suffix = owner ? `?instance_id=${encodeURIComponent(owner)}` : '';
      return forgeClient.get(`/sessions/${encodeURIComponent(id)}/runtime-version${suffix}`);
    },
    getActiveSessions: () => loadSessions('/sessions?active=true'),
    getSessionReadState: async (id, options) =>
      normalizeReadState(
        await forgeClient.get<ReadStatePayload>(
          sessionPath(id, '/read-state', options?.instanceId),
          { signal: options?.signal },
        ),
      ),
    changeSessionReadState: async (id, change, options) => {
      let payload: ReadStatePayload;
      try {
        payload = await forgeClient.patch<ReadStatePayload>(
          sessionPath(id, '/read-state', options?.instanceId),
          {
            state: change.state,
            expected_revision: change.expectedRevision,
            through_seq: change.throughSeq,
          },
        );
      } catch (error) {
        if (error instanceof ApiClientError && error.status === 409)
          throw new SessionReadStateConflictError(error.detail || undefined);
        throw error;
      }
      const readState = normalizeReadState(payload);
      const cached = sessionCache.get(id);
      if (cached) {
        sessionCache.set(id, { ...cached, readState });
        publishSessions();
      }
      return readState;
    },
    getStats: (options) =>
      options?.instanceId
        ? forgeClient
            .get<StatsPayload>(`/stats?instance_id=${encodeURIComponent(options.instanceId)}`, {
              signal: options.signal,
            })
            .then((payload) => {
              if ((payload.instance_id ?? payload.instanceId) !== options.instanceId) {
                throw new Error(
                  'This gateway does not support host-specific metrics. Update the Forge gateway.',
                );
              }
              return normalizeStats(payload);
            })
        : loadStats(options?.signal),
    getRepos: async () =>
      normalizeRepoList(
        await (niuuClient ?? forgeClient).get<
          SharedRepoResponse | SharedRepoPayload[] | VolundrRepo[]
        >('/repos'),
      ),
    listUserHome: (instanceId, path) =>
      client.get(`/storage/home?${new URLSearchParams({ instance_id: instanceId, path })}`),
    deleteUserHomePath: async (instanceId, path) => {
      await client.delete(
        `/storage/home?${new URLSearchParams({ instance_id: instanceId, path })}`,
      );
    },
    getProjects: (options) =>
      options
        ? forgeClient.get(
            `/projects${options?.instanceId ? `?instance_id=${encodeURIComponent(options.instanceId)}` : ''}`,
            { signal: options?.signal },
          )
        : forgeClient.get('/projects'),
    createProject: (name, options) =>
      forgeClient.post('/projects', {
        name,
        ...(options?.instanceId ? { instance_id: options.instanceId } : {}),
      }),
    updateProject: (project, changes) =>
      forgeClient.patch(
        `/projects/${encodeURIComponent(project.id)}${project.instance_id ? `?instance_id=${encodeURIComponent(project.instance_id)}` : ''}`,
        { ...changes, revision: project.revision },
      ),
    getProjectMembers: (project) => forgeClient.get(projectMembersPath(project)),
    getProjectMemberCandidates: (project, query) =>
      forgeClient.get(projectMembersPath(project, 'member-candidates', { q: query })),
    addProjectMember: (project, member) =>
      forgeClient.post(projectMembersPath(project), {
        user_id: member.userId,
        role: member.role,
        ...(member.expertise ? { expertise: member.expertise } : {}),
      }),
    updateProjectMember: (project, userId, change) =>
      forgeClient.patch(
        projectMembersPath(project, `members/${encodeURIComponent(userId)}`),
        change,
      ),
    removeProjectMember: async (project, userId) => {
      await forgeClient.delete(
        projectMembersPath(project, `members/${encodeURIComponent(userId)}`),
      );
    },
    getTeamIntegrationCatalog: (project) =>
      forgeClient.get(projectMembersPath(project, 'integrations/catalog')),
    getTeamIntegrations: (project) => forgeClient.get(projectMembersPath(project, 'integrations')),
    connectTeamServiceAccount: (project, connect) =>
      forgeClient.post(projectMembersPath(project, 'integrations'), connect),
    startTeamOAuth: (project, slug, config) =>
      forgeClient.post('/integrations/oauth/authorize-team', {
        slug,
        project_id: project.id,
        config,
      }),
    updateTeamIntegration: (project, connectionId, change) =>
      forgeClient.patch(
        projectMembersPath(project, `integrations/${encodeURIComponent(connectionId)}`),
        change,
      ),
    removeTeamIntegration: async (project, connectionId) => {
      await forgeClient.delete(
        projectMembersPath(project, `integrations/${encodeURIComponent(connectionId)}`),
      );
    },
    getTeamRoutes: (project) => forgeClient.get(projectMembersPath(project, 'integrations/routes')),
    setTeamRoute: (project, event, route) =>
      forgeClient.put(
        projectMembersPath(project, `integrations/routes/${encodeURIComponent(event)}`),
        route,
      ),
    removeTeamRoute: async (project, event) => {
      await forgeClient.delete(
        projectMembersPath(project, `integrations/routes/${encodeURIComponent(event)}`),
      );
    },
    getProjectMemoryMounts: (project) => forgeClient.get(projectMembersPath(project, 'memory')),
    addProjectMemoryMount: (project, mount) =>
      forgeClient.post(projectMembersPath(project, 'memory'), {
        name: mount.name,
        registry_entry_id: mount.registryEntryId,
        access: mount.access,
      }),
    removeProjectMemoryMount: async (project, name) => {
      await forgeClient.delete(projectMembersPath(project, `memory/${encodeURIComponent(name)}`));
    },
    getTeamIntegrationUsage: (project, before) =>
      forgeClient.get(projectMembersPath(project, 'integrations/usage', before ? { before } : {})),
    getSessionParticipants: async (id, options) => {
      const payload = await forgeClient.get<ParticipantPayload[]>(
        sessionPeoplePath(id, 'participants', options?.instanceId ?? cachedInstanceId(id)),
        { signal: options?.signal },
      );
      return payload.filter((entry) => entry.status !== 'revoked').map(normalizeParticipant);
    },
    getSessionProjectPeople: async (id, options) => {
      try {
        const payload = await forgeClient.get<SessionPersonPayload[]>(
          sessionPeoplePath(id, 'people', options?.instanceId ?? cachedInstanceId(id)),
          { signal: options?.signal },
        );
        return payload.map((person) => ({
          userId: person.user_id,
          displayName: person.display_name ?? '',
          projectRole: person.role,
          roomRole: roomRoleOfProjectRole(person.role),
        }));
      } catch (error) {
        // 409 is Forge saying the session has no project: an answer, not a failure.
        if (error instanceof ApiClientError && error.status === 409) return null;
        throw error;
      }
    },
    getParticipantCandidates: async (id, query, options) => {
      const payload = await forgeClient.get<
        Array<{ user_id: string; display_name: string; email: string }>
      >(
        sessionPeoplePath(
          id,
          'participant-candidates',
          options?.instanceId ?? cachedInstanceId(id),
          {
            q: query,
          },
        ),
        { signal: options?.signal },
      );
      return payload.map((person) => ({
        userId: person.user_id,
        displayName: person.display_name,
        email: person.email,
      }));
    },
    getTeamInviteCandidates: async (query, options) => {
      const params = new URLSearchParams({ q: query });
      if (options?.instanceId) params.set('instance_id', options.instanceId);
      const payload = await forgeClient.get<
        Array<{ user_id: string; display_name: string; email: string }>
      >(`/participant-candidates?${params.toString()}`, { signal: options?.signal });
      return payload.map((person) => ({
        userId: person.user_id,
        displayName: person.display_name,
        email: person.email,
      }));
    },
    inviteSessionParticipant: async (id, invitation, options) =>
      normalizeParticipant(
        await forgeClient.post<ParticipantPayload>(
          sessionPeoplePath(id, 'participants', options?.instanceId ?? cachedInstanceId(id)),
          { user_id: invitation.userId, role: invitation.role },
        ),
      ),
    acceptSessionInvitation: async (id, options) =>
      normalizeParticipant(
        await forgeClient.post<ParticipantPayload>(
          sessionPeoplePath(id, 'participants/accept', options?.instanceId ?? cachedInstanceId(id)),
        ),
      ),
    removeSessionParticipant: async (id, userId, options) => {
      await forgeClient.delete(
        sessionPeoplePath(
          id,
          `participants/${encodeURIComponent(userId)}`,
          options?.instanceId ?? cachedInstanceId(id),
        ),
      );
    },
    getSessionProject: async (id, options) => {
      const payload = await forgeClient.get<SessionProjectPayload>(
        sessionProjectPath(id, options?.instanceId),
        { signal: options?.signal },
      );
      return normalizeSessionProject(payload);
    },
    assignSessionProject: async (id, assignment, options) => {
      const payload = await forgeClient.put<SessionProjectPayload>(
        sessionProjectPath(id, options?.instanceId),
        {
          project_id: assignment.projectId,
          project_instance_id: assignment.projectInstanceId,
          expected_revision: assignment.expectedRevision,
        },
      );
      return normalizeSessionProject(payload);
    },

    getTargets: async () => {
      const targetClient = niuuClient ?? sharedClient;
      const [payload, membership] = await Promise.all([
        targetClient.get<InstanceTargetPayload[]>('/instances?kind=volundr&enabledOnly=true'),
        options.forgeScope === 'node'
          ? forgeClient.get<GuildMembershipPayload>('/guild')
          : Promise.resolve(null),
      ]);
      if (!membership) return payload.map(normalizeTarget);
      const own = payload.find((target) => target.id === membership.instance_id);
      if (!own) {
        throw new Error(
          `This Forge node is ${membership.instance_name} (${membership.instance_id}) in the ` +
            `Guild at ${membership.guild_url}, but that Guild lists no enabled Forge with ` +
            "this id. Enable the instance in the Guild registry, or correct the node's " +
            'guild.instanceId.',
        );
      }
      return [normalizeTarget(own)];
    },

    subscribe: (callback, options) => {
      sessionSubscribers.add(callback);
      ensureStream();
      if (sessionCache.size > 0) {
        callback(Array.from(sessionCache.values()));
      } else if (options?.hydrate !== false) {
        hydrateSessions();
      }
      return () => {
        sessionSubscribers.delete(callback);
        maybeCloseStream();
      };
    },
    subscribeForgeStream: (listener) => {
      streamListeners.add(listener);
      ensureStream();
      if (streamStatus) listener.onStatus?.(streamStatus);
      return () => {
        streamListeners.delete(listener);
        maybeCloseStream();
      };
    },
    subscribeStats: (callback) => {
      statsSubscribers.add(callback);
      ensureStream();
      if (statsCache) {
        callback(statsCache);
      } else {
        hydrateStats();
      }
      return () => {
        statsSubscribers.delete(callback);
        maybeCloseStream();
      };
    },

    // Unified launch specs. scope=system (read-only, config-seeded) or scope=user
    // (DB-stored, CRUD). Omitting scope returns both.
    getLaunchSpecs: (scope) =>
      catalogClient.get<VolundrLaunchSpec[]>(
        scope ? `/launch-specs?scope=${scope}` : '/launch-specs',
      ),
    getLaunchSpec: (ref) => catalogClient.get<VolundrLaunchSpec | null>(`/launch-specs/${ref}`),
    saveLaunchSpec: (spec) =>
      spec.id
        ? catalogClient.put<VolundrLaunchSpec>(`/launch-specs/${spec.id}`, spec)
        : catalogClient.post<VolundrLaunchSpec>('/launch-specs', spec),
    deleteLaunchSpec: (id) => catalogClient.delete<void>(`/launch-specs/${id}`),

    getAvailableMcpServers: () => forgeClient.get<McpServerConfig[]>('/mcp-servers'),
    getAvailableSecrets: () => client.get<string[]>('/secrets'),
    createSecret: (name, data) =>
      client.post<{ name: string; keys: string[] }>('/secrets', { name, data }),
    getClusterResources: (options) =>
      options?.instanceId
        ? forgeClient
            .get<ClusterResourceInfo>(
              `/cluster/resources?instance_id=${encodeURIComponent(options.instanceId)}`,
              { signal: options.signal },
            )
            .then((payload) => {
              if (
                payload.instances?.length !== 1 ||
                payload.instances[0]?.id !== options.instanceId
              ) {
                throw new Error(
                  'This gateway does not support host-specific resources. Update the Forge gateway.',
                );
              }
              return payload;
            })
        : options?.signal
          ? forgeClient.get<ClusterResourceInfo>('/cluster/resources', { signal: options.signal })
          : forgeClient.get<ClusterResourceInfo>('/cluster/resources'),

    startSession: async (config) =>
      normalizeSession(
        await forgeClient.post<SessionPayload>('/sessions', buildStartSessionBody(config)),
      ),
    startTeam: async (request): Promise<StartedTeam> => {
      const payload = await forgeClient.post<StartedTeamPayload>(
        '/teams',
        buildStartTeamBody(request),
      );
      return {
        session: normalizeSession(payload.session),
        members: payload.members.map(normalizeSession),
      };
    },
    getSessionMembers: async (id, options) =>
      normalizeSessionMembers(
        await forgeClient.get<SessionMembersPayload>(
          sessionPeoplePath(id, 'members', options?.instanceId ?? cachedInstanceId(id)),
          { signal: options?.signal },
        ),
      ),
    evaluatePermissionAutoApproval: async (sessionId, request) =>
      normalizePermissionAutoApproval(
        await forgeClient.post<PermissionAutoApprovalPayload>(
          sessionPath(sessionId, '/permissions/auto-approval/evaluate'),
          {
            request_id: request.requestId,
            tool_name: request.toolName,
            description: request.description,
            command: request.command ?? null,
            input: request.input ?? {},
          },
        ),
      ),
    connectSession: async (config) =>
      normalizeSession(await forgeClient.post<SessionPayload>('/sessions/connect', config)),
    updateSession: (sessionId, updates) =>
      forgeClient.put<SessionPayload>(sessionPath(sessionId), updates).then(normalizeSession),
    forkSession: async (sessionId, name) => {
      // The reforge runs on the host that holds the session's chronicle, as Lexi's fork does.
      const instanceId =
        sessionCache.get(sessionId)?.instanceId ?? archivedSessionOwners.get(sessionId);
      const chronicle = await forgeClient.get<{ id: string }>(sessionPath(sessionId, '/chronicle'));
      const reforgePath = `/chronicles/${encodeURIComponent(chronicle.id)}/reforge`;
      const child = normalizeSession(
        await forgeClient.post<SessionPayload>(
          instanceId ? `${reforgePath}?instance_id=${encodeURIComponent(instanceId)}` : reforgePath,
        ),
      );
      if (instanceId && !child.instanceId) child.instanceId = instanceId;
      sessionCache.set(child.id, child);
      publishSessions();
      // Reforge takes no name and only creates the child: name it, then start it.
      let step = 'name';
      try {
        const named = normalizeSession(
          await forgeClient.put<SessionPayload>(sessionPath(child.id), { name }),
        );
        step = 'start';
        await forgeClient.post<void>(sessionPath(child.id, '/start'));
        return { ...named, instanceId: named.instanceId ?? child.instanceId };
      } catch (error) {
        const detail =
          error instanceof ApiClientError && error.detail
            ? error.detail
            : error instanceof Error
              ? error.message
              : String(error);
        throw new SessionForkError(
          child.id,
          `The side chat ${child.name} was created, but Forge could not ${step} it: ${detail}`,
        );
      }
    },
    stopSession: (sessionId) => forgeClient.post<void>(sessionPath(sessionId, '/stop')),
    resumeSession: (sessionId) => forgeClient.post<void>(sessionPath(sessionId, '/resume')),
    deleteSession: (sessionId, cleanup) =>
      forgeClient.delete<void>(sessionPath(sessionId), {
        cleanup: cleanup ?? [],
      }),
    archiveSession: (sessionId) =>
      forgeClient.patch<void>(sessionPath(sessionId, '/archive'), undefined),
    archiveStoppedSessions: () => forgeClient.post<string[]>('/sessions/archive-stopped'),
    getSecretRequest: async (sessionId, requestId) =>
      normalizeSecretRequest(
        await forgeClient.get<SecretRequestPayload>(secretRequestPath(sessionId, requestId)),
      ),
    fulfillSecretRequest: async (sessionId, requestId, fulfillment) =>
      normalizeSecretRequest(
        await forgeClient.post<SecretRequestPayload>(
          secretRequestPath(sessionId, requestId, '/fulfill'),
          { value: fulfillment.value, keep: fulfillment.keep },
        ),
      ),
    declineSecretRequest: async (sessionId, requestId) =>
      normalizeSecretRequest(
        await forgeClient.post<SecretRequestPayload>(
          secretRequestPath(sessionId, requestId, '/decline'),
        ),
      ),
    restoreSession: (sessionId) =>
      forgeClient.patch<void>(sessionPath(sessionId, '/restore'), undefined),
    listArchivedSessions: async (options) => {
      const archivedEndpoint = `/sessions?status=archived${options?.instanceId ? `&instance_id=${encodeURIComponent(options.instanceId)}` : ''}`;
      const sessions = normalizeSessionList(
        await readSessionList(archivedEndpoint, options?.signal),
        archivedEndpoint,
      );
      if (options?.instanceId) {
        for (const [id, owner] of archivedSessionOwners) {
          if (owner === options.instanceId) archivedSessionOwners.delete(id);
        }
      } else archivedSessionOwners.clear();
      for (const session of sessions) {
        if (session.instanceId) archivedSessionOwners.set(session.id, session.instanceId);
      }
      return sessions;
    },

    listExternalSessions: () =>
      forgeClient
        .get<ExternalSessionPayload[]>('/external-sessions')
        .then((sessions) => sessions.map(normalizeExternalSession)),
    importExternalSession: async (provider, externalId, name) =>
      normalizeSession(
        await forgeClient.post<SessionPayload>('/sessions/import', {
          provider,
          external_id: externalId,
          ...(name ? { name } : {}),
        }),
      ),

    getConversationHistory: (sessionId) =>
      forgeClient
        .get<ConversationPayload>(sessionPath(sessionId, '/conversation'))
        .then(normalizeConversationHistory),
    getConversationTurn: async (sessionId, turnId) => {
      const payload = await forgeClient.get<{ turn?: ConversationPayload['turns'][number] }>(
        sessionPath(sessionId, `/conversation/turns/${encodeURIComponent(turnId)}`),
      );
      const [turn] = payload.turn
        ? normalizeConversationHistory({ turns: [payload.turn] }).turns
        : [];
      if (!turn) throw new Error(`Forge returned no turn for ${turnId}`);
      return turn;
    },
    getMessages: (sessionId) => loadMessages(sessionId),
    sendMessage: async (sessionId, content) =>
      normalizeMessageDelivery(
        sessionId,
        await forgeClient.post<SessionMessageDeliveryPayload>(sessionPath(sessionId, '/messages'), {
          content,
        }),
      ),
    subscribeMessages: (sessionId, callback) => {
      const connection = ensurePollingConnection(
        messageSubscribers,
        sessionId,
        () => loadMessages(sessionId),
        (message) => message.id,
      );
      connection.subscribers.add(callback);
      return () => {
        connection.subscribers.delete(callback);
        maybeClosePollingConnection(messageSubscribers, sessionId);
      };
    },

    getLogs: (sessionId, limit) => loadLogs(sessionId, limit),
    subscribeLogs: (sessionId, callback) => {
      const connection = ensurePollingConnection(
        logSubscribers,
        sessionId,
        () => loadLogs(sessionId),
        makeLogKey,
      );
      connection.subscribers.add(callback);
      return () => {
        connection.subscribers.delete(callback);
        maybeClosePollingConnection(logSubscribers, sessionId);
      };
    },
    getAggregatedLogs: (sessionId, options) => loadAggregatedLogs(sessionId, options),
    subscribeAggregatedLogs: (sessionId, options, callback) => {
      let disposed = false;
      let lastKey = '';
      const poll = async (): Promise<void> => {
        try {
          const payload = await loadAggregatedLogs(sessionId, options);
          const nextKey = makeAggregatedLogsKey(payload);
          if (nextKey === lastKey) return;
          lastKey = nextKey;
          if (!disposed) callback(payload);
        } catch {
          // Best-effort live polling on top of stable snapshot endpoints.
        }
      };
      void poll();
      const timer = setInterval(() => {
        void poll();
      }, LIVE_POLL_MS);
      return () => {
        disposed = true;
        clearInterval(timer);
      };
    },

    getCodeServerUrl: (sessionId) =>
      forgeClient.get<string | null>(sessionPath(sessionId, '/code-server-url')),

    getChronicle: (sessionId) => loadChronicle(sessionId),
    subscribeChronicle: (sessionId, callback) => {
      const subscribers = chronicleSubscribers.get(sessionId) ?? new Set();
      subscribers.add(callback);
      chronicleSubscribers.set(sessionId, subscribers);
      ensureStream();
      const chronicle = chronicleCache.get(sessionId);
      if (chronicle) {
        callback(chronicle);
      } else {
        hydrateChronicle(sessionId);
      }
      return () => {
        const active = chronicleSubscribers.get(sessionId);
        if (!active) return;
        active.delete(callback);
        if (active.size === 0) chronicleSubscribers.delete(sessionId);
        maybeCloseStream();
      };
    },
    getSessionTrace: async (sessionId) => {
      try {
        const payload = await forgeClient.get<TracePayload>(sessionPath(sessionId, '/trace'));
        return normalizeTrace(payload);
      } catch (error) {
        if (error instanceof Error && /404/.test(error.message)) return null;
        throw error;
      }
    },
    getSessionTraceSummary: async (sessionId) => {
      try {
        const payload = await forgeClient.get<TraceSummaryPayload>(
          sessionPath(sessionId, '/trace/summary'),
        );
        return normalizeTraceSummary(payload);
      } catch (error) {
        if (error instanceof Error && /404/.test(error.message)) return null;
        throw error;
      }
    },

    getPullRequests: (repoUrl, status) =>
      forgeClient.get<PullRequest[]>(
        `/repos/prs?url=${encodeURIComponent(repoUrl)}${status ? `&status=${status}` : ''}`,
      ),
    createPullRequest: (sessionId, title, targetBranch) =>
      forgeClient.post<PullRequest>(sessionPath(sessionId, '/pr'), { title, targetBranch }),
    mergePullRequest: (prNumber, repoUrl, mergeMethod) =>
      forgeClient.post<MergeResult>(`/repos/prs/${prNumber}/merge`, { repoUrl, mergeMethod }),
    getCIStatus: (prNumber, repoUrl, branch) =>
      forgeClient.get<CIStatusValue>(
        `/repos/prs/${prNumber}/ci?url=${encodeURIComponent(repoUrl)}&branch=${encodeURIComponent(branch)}`,
      ),

    getSessionMcpServers: (sessionId) =>
      forgeClient.get<McpServer[]>(sessionPath(sessionId, '/mcp-servers')),

    searchTrackerIssues: (query, projectId) =>
      trackerClient.get<TrackerIssue[]>(
        `/tracker/issues?q=${encodeURIComponent(query)}${projectId ? `&projectId=${projectId}` : ''}`,
      ),
    getProjectRepoMappings: () => trackerClient.get<ProjectRepoMapping[]>('/tracker/repo-mappings'),
    updateTrackerIssueStatus: (issueId, status) =>
      trackerClient.patch<TrackerIssue>(`/tracker/issues/${issueId}`, { status }),

    getIdentity: () => sharedClient.get<VolundrIdentity>('/identity/me'),
    listUsers: () => client.get<VolundrUser[]>('/admin/users'),

    getTenants: () => client.get<VolundrTenant[]>('/tenants'),
    getTenant: (id) => client.get<VolundrTenant | null>(`/tenants/${id}`),
    createTenant: (data) => client.post<VolundrTenant>('/tenants', data),
    deleteTenant: (id) => client.delete<void>(`/tenants/${id}`),
    updateTenant: (id, data) => client.patch<VolundrTenant>(`/tenants/${id}`, data),
    getTenantMembers: (tenantId) => client.get<VolundrMember[]>(`/tenants/${tenantId}/members`),
    reprovisionUser: (userId) =>
      client.post<VolundrProvisioningResult>(`/admin/users/${userId}/reprovision`),
    reprovisionTenant: (tenantId) =>
      client.post<VolundrProvisioningResult[]>(`/tenants/${tenantId}/reprovision`),

    getUserCredentials: async () => {
      const payload = await credentialsClient.get<CanonicalCredentialListPayload>('/user');
      return payload.credentials.map((credential) => ({
        name: credential.name,
        keys: credential.keys,
      }));
    },
    storeUserCredential: (name, data) => credentialsClient.post<void>('/user', { name, data }),
    deleteUserCredential: (name) => credentialsClient.delete<void>(`/user/${name}`),
    getTenantCredentials: async () => {
      const payload = await credentialsClient.get<CanonicalCredentialListPayload>('/tenant');
      return payload.credentials.map((credential) => ({
        name: credential.name,
        keys: credential.keys,
      }));
    },
    storeTenantCredential: (name, data) => credentialsClient.post<void>('/tenant', { name, data }),
    deleteTenantCredential: (name) => credentialsClient.delete<void>(`/tenant/${name}`),

    getIntegrationCatalog: async () => {
      const entries = await sharedClient.get<
        {
          id: string;
          slug: string;
          name: string;
          description: string;
          integration_type: string;
          model_vendor?: string;
        }[]
      >('/integrations/catalog');
      return entries.map((entry) => ({
        id: entry.id,
        slug: entry.slug,
        name: entry.name,
        description: entry.description,
        integrationType: entry.integration_type,
        modelVendor: entry.model_vendor ?? '',
      }));
    },
    getIntegrations: async () => {
      const connections = await sharedClient.get<
        (Partial<IntegrationConnection> & {
          id: string;
          integration_type?: string;
          credential_name?: string;
          credential_status?: string;
          created_at?: string;
          updated_at?: string;
        })[]
      >('/integrations');
      return connections.map((connection) => ({
        id: connection.id,
        slug: connection.slug,
        adapter: connection.adapter,
        enabled: connection.enabled,
        integrationType: connection.integrationType ?? connection.integration_type,
        credentialName: connection.credentialName ?? connection.credential_name,
        credentialStatus: connection.credentialStatus ?? connection.credential_status,
        config: connection.config,
        createdAt: connection.createdAt ?? connection.created_at ?? '',
        updatedAt: connection.updatedAt ?? connection.updated_at ?? '',
      }));
    },
    createIntegration: (connection) =>
      sharedClient.post<IntegrationConnection>('/integrations', connection),
    deleteIntegration: (id) => sharedClient.delete<void>(`/integrations/${id}`),
    testIntegration: (id) => sharedClient.post<IntegrationTestResult>(`/integrations/${id}/test`),

    getCredentials: async (type?: SecretType) => {
      const payload = await credentialsClient.get<CanonicalCredentialListPayload>(
        `/user${type ? `?secret_type=${type}` : ''}`,
      );
      return payload.credentials.map((credential) =>
        normalizeStoredCredential(credential, type ?? 'generic'),
      );
    },
    getCredential: async (name) => {
      const payload = await credentialsClient.get<CanonicalCredentialPayload | null>(
        `/user/${name}`,
      );
      return payload ? normalizeStoredCredential(payload) : null;
    },
    createCredential: async (req: CredentialCreateRequest) => {
      const payload = await credentialsClient.post<CanonicalCredentialPayload>('/user', {
        name: req.name,
        secret_type: req.secretType,
        data: req.data,
        metadata: req.metadata,
      });
      return normalizeStoredCredential(payload, req.secretType);
    },
    deleteCredential: (name) => credentialsClient.delete<void>(`/user/${name}`),
    getCredentialTypes: async () => {
      const payload = await credentialsClient.get<CanonicalSecretTypePayload[]>('/types');
      return payload.map(normalizeSecretTypeInfo);
    },

    listWorkspaces: (status?: WorkspaceStatus) =>
      forgeClient.get<VolundrWorkspace[]>(`/workspaces${status ? `?status=${status}` : ''}`),
    listAllWorkspaces: (status?: WorkspaceStatus) =>
      forgeClient.get<VolundrWorkspace[]>(`/admin/workspaces${status ? `?status=${status}` : ''}`),
    restoreWorkspace: (id) => forgeClient.post<void>(`/workspaces/${id}/restore`),
    deleteWorkspace: (id) => forgeClient.delete<void>(`/workspaces/${id}`),
    bulkDeleteWorkspaces: (sessionIds) =>
      forgeClient.post<{ deleted: number; failed: Array<{ session_id: string; error: string }> }>(
        '/workspaces/bulk-delete',
        { sessionIds },
      ),

    getAdminSettings: () => forgeClient.get<AdminSettings>('/admin/settings'),
    updateAdminSettings: (data: { storage?: AdminStorageSettings }) =>
      forgeClient.patch<AdminSettings>('/admin/settings', data),

    getFeatureModules: (scope?: FeatureScope) =>
      sharedClient.get<FeatureModule[]>(`/features/modules${scope ? `?scope=${scope}` : ''}`),
    toggleFeature: (key, enabled) =>
      sharedClient.post<FeatureModule>(`/features/modules/${key}/toggle`, { enabled }),
    getUserFeaturePreferences: () =>
      sharedClient.get<UserFeaturePreference[]>('/features/preferences'),
    updateUserFeaturePreferences: (preferences) =>
      sharedClient.put<UserFeaturePreference[]>('/features/preferences', preferences),

    listTokens: () => sharedClient.get<PersonalAccessToken[]>('/tokens'),
    createToken: (name, scopes) =>
      sharedClient.post<CreatePATResult>('/tokens', { name, ...(scopes ? { scopes } : {}) }),
    revokeToken: (id) => sharedClient.delete<void>(`/tokens/${id}`),
  };
}
