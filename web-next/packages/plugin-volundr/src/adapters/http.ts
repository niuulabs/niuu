/**
 * HTTP adapter for IVolundrService.
 *
 * Accepts any HTTP client with `get` and `post` / `delete` methods —
 * structurally compatible with `createApiClient(baseUrl)` from @niuulabs/query.
 */
import {
  createApiClient,
  getAuthHeaders,
  openEventStream,
  type EventStreamHandle,
  type EventStreamOptions,
} from '@niuulabs/query';
import type {
  IVolundrService,
  PermissionAutoApprovalDecision,
  VolundrConversationHistory,
} from '../ports/IVolundrService';
import type { IFileSystemPort, FileTreeNode } from '../ports/IFileSystemPort';
import type {
  VolundrSession,
  VolundrStats,
  VolundrFeatures,
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
  VolundrLaunchSpec,
  TrackerIssue,
  ProjectRepoMapping,
  VolundrIdentity,
  VolundrUser,
  VolundrTenant,
  IntegrationConnection,
  IntegrationTestResult,
  CatalogEntry,
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
  VolundrWorkflowGate,
  VolundrSessionTrace,
  VolundrSessionTraceLane,
  VolundrSessionTraceSpan,
  VolundrSessionTraceSummary,
} from '../models/volundr.model';

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
}

interface FileEntryPayload {
  name: string;
  path: string;
  type: 'file' | 'directory';
  size?: number;
}

interface FileListPayload {
  entries: FileEntryPayload[];
}

type SessionPayload = {
  id: string;
  name: string;
  source: VolundrSession['source'];
  status: VolundrSession['status'];
  model: string;
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
  ownerId?: string | null;
  owner_id?: string | null;
  tenantId?: string | null;
  tenant_id?: string | null;
  instanceId?: string | null;
  instance_id?: string | null;
  instanceName?: string | null;
  instance_name?: string | null;
};

type StatsPayload = {
  activeSessions?: number;
  active_sessions?: number;
  totalSessions?: number;
  total_sessions?: number;
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
  }>;
};

type WorkflowGatePayload = {
  id: string;
  node_id?: string;
  activation_id?: string;
  label?: string;
  condition?: string;
  status?: VolundrWorkflowGate['status'];
  pending_behavior?: VolundrWorkflowGate['pending_behavior'];
  approvers?: string[];
  auto_forward_after?: string;
  requested_at?: string;
  updated_at?: string;
  triggered_by_event_type?: string;
  approval_event_type?: string;
  changes_requested_event_type?: string;
  attempt?: number;
  decision?: string | null;
  notes?: string;
  source?: string;
  summary?: string;
};

type WorkflowGateListPayload = {
  gates?: WorkflowGatePayload[];
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
  id: string;
  slug: string;
  name: string;
  baseUrl?: string;
  base_url?: string;
  enabled: boolean;
  isDefault?: boolean;
  is_default?: boolean;
  visibility?: string;
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
    source: config.source,
    model: config.model,
    definition: config.definition,
    launch_spec: config.launchSpec,
    launch_spec_id: config.launchSpecId,
    terminal_restricted: Boolean(config.terminalRestricted),
    instance_id: config.instanceId ?? null,
    workspace_id: config.workspaceId,
    credential_names: config.credentialNames,
    integration_ids: config.integrationIds,
    resource_config: config.resourceConfig,
    system_prompt: config.systemPrompt,
    initial_prompt: config.initialPrompt,
    workload_config: config.workloadConfig ?? {},
    issue_id: config.trackerIssue?.id,
    issue_url: config.trackerIssue?.url,
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

function normalizeSession(session: SessionPayload): VolundrSession {
  const trackerIssue =
    session.trackerIssue ??
    (() => {
      const identifier = session.trackerIssueId ?? session.tracker_issue_id ?? null;
      const url = session.issueTrackerUrl ?? session.issue_tracker_url ?? '';
      if (!identifier) return undefined;
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
    source: session.source,
    status: session.status,
    model: session.model,
    lastActive: toEpochMs(session.lastActive ?? session.last_active),
    messageCount: session.messageCount ?? session.message_count ?? 0,
    tokensUsed: session.tokensUsed ?? session.tokens_used ?? 0,
    podName: session.podName ?? session.pod_name ?? undefined,
    error: session.error ?? undefined,
    origin: session.origin,
    hostname: session.hostname,
    chatEndpoint: session.chatEndpoint ?? session.chat_endpoint ?? undefined,
    codeEndpoint: session.codeEndpoint ?? session.code_endpoint ?? undefined,
    taskType: session.taskType ?? session.task_type ?? undefined,
    archivedAt: toDate(session.archivedAt ?? session.archived_at),
    trackerIssue,
    activityState: session.activityState ?? session.activity_state ?? undefined,
    ownerId: session.ownerId ?? session.owner_id ?? undefined,
    tenantId: session.tenantId ?? session.tenant_id ?? undefined,
    instanceId: session.instanceId ?? session.instance_id ?? undefined,
    instanceName: session.instanceName ?? session.instance_name ?? undefined,
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
  };
}

function normalizeStats(stats: StatsPayload): VolundrStats {
  return {
    activeSessions: stats.activeSessions ?? stats.active_sessions ?? 0,
    totalSessions: stats.totalSessions ?? stats.total_sessions ?? 0,
    tokensToday: stats.tokensToday ?? stats.tokens_today ?? 0,
    localTokens: stats.localTokens ?? stats.local_tokens ?? 0,
    cloudTokens: stats.cloudTokens ?? stats.cloud_tokens ?? 0,
    costToday: stats.costToday ?? stats.cost_today ?? 0,
    sparklines: stats.sparklines,
  };
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
  return null;
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
    })),
  };
}

function normalizeWorkflowGate(payload: WorkflowGatePayload): VolundrWorkflowGate {
  return {
    id: payload.id,
    node_id: payload.node_id ?? '',
    activation_id: payload.activation_id ?? '',
    label: payload.label ?? payload.id,
    condition: payload.condition ?? '',
    status: payload.status ?? 'pending',
    pending_behavior: payload.pending_behavior ?? 'help_needed',
    approvers: payload.approvers ?? [],
    auto_forward_after: payload.auto_forward_after ?? '30m',
    requested_at: payload.requested_at ?? new Date(0).toISOString(),
    updated_at: payload.updated_at ?? payload.requested_at ?? new Date(0).toISOString(),
    triggered_by_event_type: payload.triggered_by_event_type ?? '',
    approval_event_type: payload.approval_event_type ?? '',
    changes_requested_event_type: payload.changes_requested_event_type ?? '',
    attempt: payload.attempt ?? 1,
    decision: payload.decision ?? undefined,
    notes: payload.notes ?? '',
    source: payload.source ?? 'human',
    summary: payload.summary ?? '',
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

  return Object.values(payload).flat().map(normalizeRepo);
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
  toEpochMs,
  toDate,
  normalizeSession,
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
  normalizeWorkflowGate,
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
    return `${baseUrl}/s/${encodeURIComponent(sessionId)}/api`;
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

export function buildVolundrHttpAdapter(
  client: HttpClient,
  openStream: EventStreamOpener = openEventStream,
  options: VolundrHttpAdapterOptions = {},
): IVolundrService {
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
  const chronicleCache = new Map<string, SessionChronicle>();
  let statsCache: VolundrStats | null = null;
  let streamHandle: EventStreamHandle | null = null;
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

  function updateSessionCache(sessions: VolundrSession[]): void {
    sessionCache.clear();
    for (const session of sessions) sessionCache.set(session.id, session);
  }

  async function loadSessions(endpoint: string): Promise<VolundrSession[]> {
    const sessions = (await forgeClient.get<SessionPayload[]>(endpoint)).map(normalizeSession);
    updateSessionCache(sessions);
    publishSessions();
    return sessions;
  }

  async function loadSession(id: string): Promise<VolundrSession | null> {
    const session = await forgeClient.get<SessionPayload | null>(`/sessions/${id}`);
    if (!session) {
      sessionCache.delete(id);
      publishSessions();
      return null;
    }
    const normalized = normalizeSession(session);
    sessionCache.set(normalized.id, normalized);
    publishSessions();
    return normalized;
  }

  async function loadStats(): Promise<VolundrStats> {
    statsCache = normalizeStats(await forgeClient.get<StatsPayload>('/stats'));
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
      .get<ConversationPayload>(`/sessions/${sessionId}/conversation`)
      .then((payload) => normalizeMessages(sessionId, payload));
  }

  async function loadLogs(sessionId: string, limit?: number): Promise<VolundrLog[]> {
    return forgeClient
      .get<LogPayload>(`/sessions/${sessionId}/logs${limit ? `?lines=${limit}` : ''}`)
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
        `/sessions/${sessionId}/logs/aggregate${buildAggregatedLogsQuery(options)}`,
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

  function ensureStream(): void {
    if (streamHandle || !forgeClient.basePath) return;
    streamHandle = openStream(`${forgeClient.basePath}/sessions/stream`, {
      onMessage: () => {},
      onEvent: ({ event, data }) => {
        try {
          const payload = JSON.parse(data) as SessionPayload | StatsPayload | { id?: string };
          const eventType = event ?? inferEventType(payload);
          if (eventType === 'session_created' || eventType === 'session_updated') {
            const session = normalizeSession(payload as SessionPayload);
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
          if (eventType === 'stats_updated') {
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
    if (sessionSubscribers.size > 0 || statsSubscribers.size > 0 || hasChronicleSubscribers) return;
    streamHandle?.close();
    streamHandle = null;
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
    getFeatures: () => sharedClient.get<VolundrFeatures>('/features'),
    getSessionDefinitions: async () => {
      const payload = await catalogClient.get<SessionDefinitionPayload[]>('/session-definitions');
      return payload.map(normalizeSessionDefinition);
    },
    getSessions: () => loadSessions('/sessions'),
    getSession: (id) => loadSession(id),
    getActiveSessions: () => loadSessions('/sessions?active=true'),
    getStats: () => loadStats(),
    getRepos: async () =>
      normalizeRepoList(
        await (niuuClient ?? forgeClient).get<
          SharedRepoResponse | SharedRepoPayload[] | VolundrRepo[]
        >('/repos'),
      ),
    getTargets: async () => {
      const targetClient = niuuClient ?? sharedClient;
      const payload = await targetClient.get<InstanceTargetPayload[]>(
        '/instances?kind=volundr&enabledOnly=true',
      );
      return payload.map(normalizeTarget);
    },

    subscribe: (callback) => {
      sessionSubscribers.add(callback);
      ensureStream();
      if (sessionCache.size > 0) {
        callback(Array.from(sessionCache.values()));
      } else {
        hydrateSessions();
      }
      return () => {
        sessionSubscribers.delete(callback);
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
    getClusterResources: () => forgeClient.get<ClusterResourceInfo>('/cluster/resources'),

    startSession: async (config) =>
      normalizeSession(
        await forgeClient.post<SessionPayload>('/sessions', buildStartSessionBody(config)),
      ),
    evaluatePermissionAutoApproval: async (sessionId, request) =>
      normalizePermissionAutoApproval(
        await forgeClient.post<PermissionAutoApprovalPayload>(
          `/sessions/${sessionId}/permissions/auto-approval/evaluate`,
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
      forgeClient.patch<SessionPayload>(`/sessions/${sessionId}`, updates).then(normalizeSession),
    stopSession: (sessionId) => forgeClient.post<void>(`/sessions/${sessionId}/stop`),
    resumeSession: (sessionId) => forgeClient.post<void>(`/sessions/${sessionId}/resume`),
    deleteSession: (sessionId, cleanup) =>
      forgeClient.delete<void>(`/sessions/${sessionId}`, {
        cleanup: cleanup ?? [],
      }),
    archiveSession: (sessionId) =>
      forgeClient.patch<void>(`/sessions/${sessionId}/archive`, undefined),
    archiveStoppedSessions: () => forgeClient.post<string[]>('/sessions/archive-stopped'),
    restoreSession: (sessionId) =>
      forgeClient.patch<void>(`/sessions/${sessionId}/restore`, undefined),
    listArchivedSessions: () =>
      forgeClient
        .get<SessionPayload[]>('/sessions?status=archived')
        .then((sessions) => sessions.map(normalizeSession)),

    getConversationHistory: (sessionId) =>
      forgeClient
        .get<ConversationPayload>(`/sessions/${sessionId}/conversation`)
        .then(normalizeConversationHistory),
    getWorkflowGates: async (sessionId) => {
      const payload = await forgeClient.get<WorkflowGateListPayload>(
        `/sessions/${sessionId}/workflow/gates`,
      );
      return (payload.gates ?? []).map(normalizeWorkflowGate);
    },
    resolveWorkflowGate: async (sessionId, gateId, request) => {
      const response = await fetch(
        `${forgeClient.basePath}/sessions/${sessionId}/workflow/gates/${gateId}/resolve`,
        {
          method: 'POST',
          headers: getAuthHeaders({
            'Content-Type': 'application/json',
            'x-niuu-workflow-gate-intent': 'resolve',
          }),
          body: JSON.stringify(request),
        },
      );
      const payload = await response.json();
      if (!response.ok) {
        const detail =
          payload && typeof payload === 'object' && 'detail' in payload
            ? String((payload as { detail?: unknown }).detail ?? 'Unknown error')
            : 'Unknown error';
        throw new Error(`API request failed: ${response.status} ${detail}`);
      }
      return normalizeWorkflowGate(payload as WorkflowGatePayload);
    },
    getMessages: (sessionId) => loadMessages(sessionId),
    sendMessage: (sessionId, content) =>
      forgeClient.post<VolundrMessage>(`/sessions/${sessionId}/messages`, { content }),
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
      forgeClient.get<string | null>(`/sessions/${sessionId}/code-server-url`),

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
        const payload = await forgeClient.get<TracePayload>(`/sessions/${sessionId}/trace`);
        return normalizeTrace(payload);
      } catch (error) {
        if (error instanceof Error && /404/.test(error.message)) return null;
        throw error;
      }
    },
    getSessionTraceSummary: async (sessionId) => {
      try {
        const payload = await forgeClient.get<TraceSummaryPayload>(
          `/sessions/${sessionId}/trace/summary`,
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
      forgeClient.post<PullRequest>(`/sessions/${sessionId}/pr`, { title, targetBranch }),
    mergePullRequest: (prNumber, repoUrl, mergeMethod) =>
      forgeClient.post<MergeResult>(`/repos/prs/${prNumber}/merge`, { repoUrl, mergeMethod }),
    getCIStatus: (prNumber, repoUrl, branch) =>
      forgeClient.get<CIStatusValue>(
        `/repos/prs/${prNumber}/ci?url=${encodeURIComponent(repoUrl)}&branch=${encodeURIComponent(branch)}`,
      ),

    getSessionMcpServers: (sessionId) =>
      forgeClient.get<McpServer[]>(`/sessions/${sessionId}/mcp-servers`),

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

    getIntegrationCatalog: () => sharedClient.get<CatalogEntry[]>('/integrations/catalog'),
    getIntegrations: () => sharedClient.get<IntegrationConnection[]>('/integrations'),
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
    createToken: (name) => sharedClient.post<CreatePATResult>('/tokens', { name }),
    revokeToken: (id) => sharedClient.delete<void>(`/tokens/${id}`),
  };
}
