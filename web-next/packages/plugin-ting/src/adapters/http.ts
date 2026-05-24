/**
 * HTTP adapters for all Ting ports.
 *
 * Adapted from web/src/modules/ting/adapters/api/
 * Translates snake_case server responses to camelCase domain types.
 *
 * All factory functions accept an ApiClient structurally compatible with
 * @niuulabs/query (get / post / put / patch / delete methods).
 */

import type { ApiClient } from '@niuulabs/query';
import type {
  ITingService,
  IDispatcherService,
  ITingSessionService,
  ITrackerBrowserService,
  IDispatchBus,
  DispatchResult,
  DispatchQueueItem,
  DispatchApprovalItem,
  DispatchApprovalOptions,
  DispatchApprovalResult,
  DispatchCluster,
  IWorkflowService,
  WorkflowLaunchRequest,
  WorkflowLaunchResult,
  ITingSettingsService,
  IAuditLogService,
  DispatcherActivityEvent,
  CommitSagaRequest,
  PlanSession,
  ExtractedStructure,
  PhaseSpec,
  RunSessionMessage,
  RunHelpRequest,
  FlockConfig,
  DispatchDefaults,
  NotificationSettings,
  AuditEntry,
  AuditFilter,
} from '../ports';
import type { Saga, Phase, Run } from '../domain/saga';
import type { DispatcherState } from '../domain/dispatcher';
import type { SessionInfo } from '../domain/session';
import type { TrackerProject, TrackerMilestone, TrackerIssue } from '../domain/tracker';
import type { Workflow } from '../domain/workflow';

// ---------------------------------------------------------------------------
// Raw server types (snake_case)
// ---------------------------------------------------------------------------

interface RawSaga {
  id: string;
  tracker_id: string;
  tracker_type?: string;
  slug?: string;
  name: string;
  repos: string[];
  feature_branch: string;
  base_branch?: string;
  status: string;
  confidence?: number;
  created_at: string;
  workflow_id?: string | null;
  workflow?: string | null;
  workflow_version?: string | null;
  instance_id?: string | null;
  instance_name?: string | null;
  phase_summary?: {
    total: number;
    completed: number;
  } | null;
  phase_count?: number;
  run_count?: number;
}

interface RawRun {
  id: string;
  phase_id: string;
  tracker_id: string;
  name: string;
  description: string;
  acceptance_criteria: string[];
  declared_files: string[];
  estimate_hours: number | null;
  status: string;
  confidence: number;
  session_id: string | null;
  reviewer_session_id: string | null;
  review_round: number;
  branch: string | null;
  chronicle_summary: string | null;
  retry_count: number;
  created_at: string;
  updated_at: string;
}

interface RawPhase {
  id: string;
  saga_id: string;
  tracker_id: string;
  number: number;
  name: string;
  status: string;
  confidence: number;
  runs: RawRun[];
}

interface RawDispatcherState {
  id: string;
  running: boolean;
  threshold: number;
  max_concurrent_runs: number;
  auto_continue: boolean;
  updated_at: string;
}

interface RawDispatcherActivityEvent {
  id: string;
  event: string;
  data: Record<string, unknown>;
  owner_id: string;
  timestamp: string;
}

interface RawDispatcherActivityLog {
  events: RawDispatcherActivityEvent[];
  total: number;
}

interface RawSessionInfo {
  session_id: string;
  status: string;
  chronicle_lines: string[];
  branch: string | null;
  confidence: number;
  run_name: string;
  saga_name: string;
  cluster_name: string;
}

interface RawHelpRequest {
  summary: string;
  reason: string;
  attempted: string[];
  recommendation?: string | null;
  context: Record<string, unknown>;
  target_peer_id?: string | null;
  persona?: string | null;
}

interface RawSessionMessage {
  id: string;
  session_id: string;
  content: string;
  sender: string;
  created_at: string;
  kind?: 'message' | 'help_request';
  help_request?: RawHelpRequest | null;
}

interface RawSendMessageResponse {
  message_id: string;
  run_id: string;
  session_id: string;
  content: string;
  sender: string;
  created_at: string;
}

interface RawTrackerProject {
  id: string;
  name: string;
  description: string;
  status: string;
  url: string;
  milestone_count: number;
  issue_count: number;
  slug?: string;
}

interface RawTrackerMilestone {
  id: string;
  project_id: string;
  name: string;
  description: string;
  sort_order: number;
  progress: number;
}

interface RawTrackerIssue {
  id: string;
  identifier: string;
  title: string;
  description: string;
  status: string;
  assignee: string | null;
  labels: string[];
  priority: number;
  url: string;
  milestone_id: string | null;
}

interface RawDispatchQueueItem {
  saga_id: string;
  saga_name: string;
  saga_slug: string;
  repos: string[];
  feature_branch: string;
  phase_name: string;
  issue_id: string;
  identifier: string;
  title: string;
  description: string;
  status: string;
  priority: number;
  priority_label: string;
  estimate: number | null;
  url: string;
  workflow_id?: string | null;
  workflow?: string | null;
  workflow_version?: string | null;
  instance_id?: string | null;
}

interface RawDispatchApprovalResult {
  issue_id: string;
  session_id: string;
  session_name: string;
  status: string;
  cluster_name: string;
}

interface RawDispatchCluster {
  connection_id: string;
  instance_id?: string;
  name: string;
  url: string;
  enabled: boolean;
}

interface RawWorkflow {
  id: string;
  name: string;
  description: string;
  version: string;
  scope: 'system' | 'user';
  owner_id: string | null;
  nodes: Workflow['nodes'];
  edges: Workflow['edges'];
  resourceBindings?: Workflow['resourceBindings'];
  resource_bindings?: Workflow['resourceBindings'];
}

interface RawWorkflowLaunchResult {
  workflowId?: string;
  workflow_id?: string;
  workflowName?: string;
  workflow_name?: string;
  slug: string;
  sessionId?: string;
  session_id?: string;
  sessionName?: string;
  session_name?: string;
  status: string;
  clusterName?: string;
  cluster_name?: string;
}

// ---------------------------------------------------------------------------
// Transform functions
// ---------------------------------------------------------------------------

function toRun(raw: RawRun): Run {
  return {
    id: raw.id,
    phaseId: raw.phase_id,
    trackerId: raw.tracker_id,
    name: raw.name,
    description: raw.description,
    acceptanceCriteria: raw.acceptance_criteria,
    declaredFiles: raw.declared_files,
    estimateHours: raw.estimate_hours,
    status: raw.status as Run['status'],
    confidence: raw.confidence,
    sessionId: raw.session_id,
    reviewerSessionId: raw.reviewer_session_id,
    reviewRound: raw.review_round,
    branch: raw.branch,
    chronicleSummary: raw.chronicle_summary,
    retryCount: raw.retry_count,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
  };
}

function toPhase(raw: RawPhase): Phase {
  return {
    id: raw.id,
    sagaId: raw.saga_id,
    trackerId: raw.tracker_id,
    number: raw.number,
    name: raw.name,
    status: raw.status as Phase['status'],
    confidence: raw.confidence,
    runs: raw.runs.map(toRun),
  };
}

function toSaga(raw: RawSaga): Saga {
  const phaseSummary = raw.phase_summary ?? {
    total: raw.run_count ?? 0,
    completed: 0,
  };
  return {
    id: raw.id,
    trackerId: raw.tracker_id,
    trackerType: raw.tracker_type ?? 'linear',
    slug:
      raw.slug ??
      raw.name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, ''),
    name: raw.name,
    repos: raw.repos,
    featureBranch: raw.feature_branch,
    baseBranch: raw.base_branch ?? 'main',
    status: raw.status as Saga['status'],
    confidence: raw.confidence ?? 0,
    createdAt: raw.created_at,
    workflowId: raw.workflow_id ?? undefined,
    workflow: raw.workflow ?? undefined,
    workflowVersion: raw.workflow_version ?? undefined,
    instanceId: raw.instance_id ?? undefined,
    instanceName: raw.instance_name ?? undefined,
    phaseSummary: {
      total: phaseSummary.total,
      completed: phaseSummary.completed,
    },
  };
}

function toDispatcherState(raw: RawDispatcherState): DispatcherState {
  return {
    id: raw.id,
    running: raw.running,
    threshold: raw.threshold,
    maxConcurrentRuns: raw.max_concurrent_runs,
    autoContinue: raw.auto_continue,
    updatedAt: raw.updated_at,
  };
}

function toSessionInfo(raw: RawSessionInfo): SessionInfo {
  return {
    sessionId: raw.session_id,
    status: raw.status as SessionInfo['status'],
    chronicleLines: raw.chronicle_lines,
    branch: raw.branch,
    confidence: raw.confidence,
    runName: raw.run_name,
    sagaName: raw.saga_name,
    clusterName: raw.cluster_name,
  };
}

function toDispatcherActivityEvent(raw: RawDispatcherActivityEvent): DispatcherActivityEvent {
  return {
    id: raw.id,
    event: raw.event,
    data: raw.data,
    ownerId: raw.owner_id,
    timestamp: raw.timestamp,
  };
}

function toHelpRequest(raw: RawHelpRequest): RunHelpRequest {
  return {
    summary: raw.summary,
    reason: raw.reason,
    attempted: raw.attempted ?? [],
    recommendation: raw.recommendation ?? undefined,
    context: raw.context ?? {},
    targetPeerId: raw.target_peer_id ?? undefined,
    persona: raw.persona ?? undefined,
  };
}

function toRunSessionMessage(raw: RawSessionMessage): RunSessionMessage {
  return {
    id: raw.id,
    sessionId: raw.session_id,
    content: raw.content,
    sender: raw.sender,
    createdAt: raw.created_at,
    kind: raw.kind ?? (raw.help_request ? 'help_request' : 'message'),
    helpRequest: raw.help_request ? toHelpRequest(raw.help_request) : null,
  };
}

function toTrackerProject(raw: RawTrackerProject): TrackerProject {
  return {
    id: raw.id,
    name: raw.name,
    description: raw.description,
    status: raw.status,
    url: raw.url,
    milestoneCount: raw.milestone_count,
    issueCount: raw.issue_count,
    slug: raw.slug ?? '',
  };
}

function toTrackerMilestone(raw: RawTrackerMilestone): TrackerMilestone {
  return {
    id: raw.id,
    projectId: raw.project_id,
    name: raw.name,
    description: raw.description,
    sortOrder: raw.sort_order,
    progress: raw.progress,
  };
}

function toTrackerIssue(raw: RawTrackerIssue): TrackerIssue {
  return {
    id: raw.id,
    identifier: raw.identifier,
    title: raw.title,
    description: raw.description,
    status: raw.status,
    assignee: raw.assignee,
    labels: raw.labels,
    priority: raw.priority,
    url: raw.url,
    milestoneId: raw.milestone_id,
  };
}

function toDispatchQueueItem(raw: RawDispatchQueueItem): DispatchQueueItem {
  return {
    sagaId: raw.saga_id,
    sagaName: raw.saga_name,
    sagaSlug: raw.saga_slug,
    repos: raw.repos,
    featureBranch: raw.feature_branch,
    phaseName: raw.phase_name,
    issueId: raw.issue_id,
    identifier: raw.identifier,
    title: raw.title,
    description: raw.description,
    status: raw.status,
    priority: raw.priority,
    priorityLabel: raw.priority_label,
    estimate: raw.estimate,
    url: raw.url,
    workflowId: raw.workflow_id ?? undefined,
    workflow: raw.workflow ?? undefined,
    workflowVersion: raw.workflow_version ?? undefined,
    instanceId: raw.instance_id ?? undefined,
  };
}

function toDispatchApprovalResult(raw: RawDispatchApprovalResult): DispatchApprovalResult {
  return {
    issueId: raw.issue_id,
    sessionId: raw.session_id,
    sessionName: raw.session_name,
    status: raw.status,
    clusterName: raw.cluster_name,
  };
}

function toDispatchCluster(raw: RawDispatchCluster): DispatchCluster {
  return {
    instanceId: raw.instance_id,
    connectionId: raw.connection_id,
    name: raw.name,
    url: raw.url,
    enabled: raw.enabled,
  };
}

function toCommitRequestBody(req: CommitSagaRequest): Record<string, unknown> {
  return {
    name: req.name,
    slug: req.slug,
    description: req.description,
    repos: req.repos,
    base_branch: req.baseBranch,
    phases: req.phases.map((p) => ({
      name: p.name,
      runs: p.runs.map((r) => ({
        name: r.name,
        description: r.description,
        acceptance_criteria: r.acceptanceCriteria,
        declared_files: r.declaredFiles,
        estimate_hours: r.estimateHours,
      })),
    })),
    transcript: req.transcript,
  };
}

function toWorkflow(raw: RawWorkflow): Workflow {
  const nodes = raw.nodes.map((node, index) => ({
    ...node,
    position: node.position ?? { x: 96 + index * 240, y: 144 },
  }));

  const positions = new Map(nodes.map((node) => [node.id, node.position]));
  const edges = raw.edges.map((edge) => {
    if (edge.cp1 && edge.cp2) {
      return edge;
    }

    const source = positions.get(edge.source);
    const target = positions.get(edge.target);
    const isMostlyHorizontal =
      source && target ? Math.abs(target.x - source.x) >= Math.abs(target.y - source.y) : true;

    return {
      ...edge,
      cp1: edge.cp1 ?? (isMostlyHorizontal ? { x: 92, y: 0 } : { x: 0, y: 92 }),
      cp2: edge.cp2 ?? (isMostlyHorizontal ? { x: -92, y: 0 } : { x: 0, y: -92 }),
    };
  });

  return {
    id: raw.id,
    name: raw.name,
    description: raw.description || undefined,
    version: raw.version || undefined,
    scope: raw.scope,
    ownerId: raw.owner_id,
    nodes,
    edges,
    resourceBindings: raw.resourceBindings ?? raw.resource_bindings ?? [],
  };
}

function toWorkflowBody(workflow: Workflow): Record<string, unknown> {
  return {
    name: workflow.name,
    description: workflow.description ?? '',
    version: workflow.version ?? 'draft',
    scope: workflow.scope ?? 'user',
    nodes: workflow.nodes,
    edges: workflow.edges,
    resourceBindings: workflow.resourceBindings ?? [],
  };
}

function toWorkflowLaunchBody(request: WorkflowLaunchRequest): Record<string, unknown> {
  return {
    prompt: request.prompt,
    sessionName: request.sessionName,
    repo: request.repo,
    branch: request.branch,
  };
}

function toWorkflowLaunchResult(raw: RawWorkflowLaunchResult): WorkflowLaunchResult {
  return {
    workflowId: raw.workflowId ?? raw.workflow_id ?? '',
    workflowName: raw.workflowName ?? raw.workflow_name ?? '',
    slug: raw.slug,
    sessionId: raw.sessionId ?? raw.session_id ?? '',
    sessionName: raw.sessionName ?? raw.session_name ?? '',
    status: raw.status,
    clusterName: raw.clusterName ?? raw.cluster_name ?? '',
  };
}

// ---------------------------------------------------------------------------
// Factory functions
// ---------------------------------------------------------------------------

/**
 * Build an ITingService backed by the Ting REST API.
 *
 * @param client - HTTP client scoped to the Ting sagas base path.
 */
export function buildTingHttpAdapter(client: ApiClient): ITingService {
  return {
    async getSagas() {
      const raw = await client.get<RawSaga[]>('/sagas');
      return raw.map(toSaga);
    },

    async getSaga(id: string) {
      try {
        const raw = await client.get<RawSaga>(`/sagas/${encodeURIComponent(id)}`);
        return toSaga(raw);
      } catch {
        return null;
      }
    },

    async getPhases(sagaId: string) {
      const raw = await client.get<RawPhase[]>(`/sagas/${encodeURIComponent(sagaId)}/phases`);
      return raw.map(toPhase);
    },

    async listRunMessages(runId: string) {
      const raw = await client.get<RawSessionMessage[]>(
        `/runs/${encodeURIComponent(runId)}/messages`,
      );
      return raw.map(toRunSessionMessage);
    },

    async sendRunMessage(runId: string, content: string, targetPeerId?: string) {
      const raw = await client.post<RawSendMessageResponse>(
        `/runs/${encodeURIComponent(runId)}/message`,
        {
          content,
          target_peer_id: targetPeerId ?? null,
        },
      );
      return toRunSessionMessage({
        id: raw.message_id,
        session_id: raw.session_id,
        content: raw.content,
        sender: raw.sender,
        created_at: raw.created_at,
        kind: 'message',
      });
    },

    async createSaga(spec: string, repo: string) {
      const raw = await client.post<RawSaga>('/sagas', { spec, repo });
      return toSaga(raw);
    },

    async commitSaga(request: CommitSagaRequest) {
      const raw = await client.post<RawSaga>('/sagas/commit', toCommitRequestBody(request));
      return toSaga(raw);
    },

    async decompose(spec: string, repo: string) {
      const raw = await client.post<RawPhase[]>('/sagas/decompose', { spec, repo });
      return raw.map(toPhase);
    },

    async spawnPlanSession(spec: string, repo: string) {
      const raw = await client.post<{
        session_id: string;
        chat_endpoint: string | null;
        questions?: { id: string; question: string; hint?: string }[];
      }>('/sagas/plan', { spec, repo });
      return {
        sessionId: raw.session_id,
        chatEndpoint: raw.chat_endpoint,
        questions: raw.questions ?? [],
      } satisfies PlanSession;
    },

    async extractStructure(text: string) {
      const raw = await client.post<{
        found: boolean;
        structure: { name: string; phases: PhaseSpec[] } | null;
      }>('/sagas/extract-structure', { text });
      return raw satisfies ExtractedStructure;
    },

    async assignWorkflow(sagaId: string, workflowId: string | null) {
      const raw = await client.put<RawSaga>(`/sagas/${encodeURIComponent(sagaId)}/workflow`, {
        workflow_id: workflowId,
      });
      return toSaga(raw);
    },

    async assignTarget(sagaId: string, instanceId: string | null) {
      const raw = await client.put<RawSaga>(`/sagas/${encodeURIComponent(sagaId)}/target`, {
        instance_id: instanceId,
      });
      return toSaga(raw);
    },
  };
}

/**
 * Build an IDispatcherService backed by the Ting dispatcher API.
 *
 * @param client - HTTP client scoped to the dispatcher base path.
 */
export function buildDispatcherHttpAdapter(client: ApiClient): IDispatcherService {
  return {
    async getState() {
      try {
        const raw = await client.get<RawDispatcherState>('/dispatcher');
        return toDispatcherState(raw);
      } catch {
        return null;
      }
    },

    async setRunning(running: boolean) {
      await client.patch<void>('/dispatcher', { running });
    },

    async setThreshold(threshold: number) {
      await client.patch<void>('/dispatcher', { threshold });
    },

    async setAutoContinue(autoContinue: boolean) {
      await client.patch<void>('/dispatcher', { auto_continue: autoContinue });
    },

    async getLog() {
      return client.get<string[]>('/dispatcher/log');
    },

    async getActivityLog(limit = 100) {
      const raw = await client.get<RawDispatcherActivityLog>(`/dispatcher/log?limit=${limit}`);
      return raw.events.map(toDispatcherActivityEvent);
    },
  };
}

/**
 * Build an ITingSessionService backed by the Ting sessions API.
 *
 * @param client - HTTP client scoped to the sessions base path.
 */
export function buildTingSessionHttpAdapter(client: ApiClient): ITingSessionService {
  return {
    async getSessions() {
      const raw = await client.get<RawSessionInfo[]>('/sessions');
      return raw.map(toSessionInfo);
    },

    async getSession(id: string) {
      try {
        const raw = await client.get<RawSessionInfo>(`/sessions/${encodeURIComponent(id)}`);
        return toSessionInfo(raw);
      } catch {
        return null;
      }
    },

    async approve(sessionId: string) {
      await client.post<void>(`/sessions/${encodeURIComponent(sessionId)}/approve`, {});
    },
  };
}

/**
 * Build an ITrackerBrowserService backed by the Ting tracker API.
 *
 * @param client - HTTP client scoped to the tracker base path.
 */
export function buildTrackerHttpAdapter(client: ApiClient): ITrackerBrowserService {
  return {
    async listProjects() {
      const raw = await client.get<RawTrackerProject[]>('/tracker/projects');
      return raw.map(toTrackerProject);
    },

    async getProject(projectId: string) {
      const raw = await client.get<RawTrackerProject>(
        `/tracker/projects/${encodeURIComponent(projectId)}`,
      );
      return toTrackerProject(raw);
    },

    async listMilestones(projectId: string) {
      const raw = await client.get<RawTrackerMilestone[]>(
        `/tracker/projects/${encodeURIComponent(projectId)}/milestones`,
      );
      return raw.map(toTrackerMilestone);
    },

    async listIssues(projectId: string, milestoneId?: string) {
      const query = milestoneId ? `?milestone_id=${encodeURIComponent(milestoneId)}` : '';
      const raw = await client.get<RawTrackerIssue[]>(
        `/tracker/projects/${encodeURIComponent(projectId)}/issues${query}`,
      );
      return raw.map(toTrackerIssue);
    },

    async importProject(
      projectId: string,
      repos: string[],
      baseBranch?: string,
      instanceId?: string | null,
    ) {
      const raw = await client.post<RawSaga>('/tracker/import', {
        project_id: projectId,
        repos,
        base_branch: baseBranch,
        instance_id: instanceId ?? null,
      });
      return toSaga(raw);
    },
  };
}

// ---------------------------------------------------------------------------
// Dispatch bus (Sleipnir) HTTP adapter
// ---------------------------------------------------------------------------

export function buildDispatchBusHttpAdapter(client: ApiClient): IDispatchBus {
  return {
    async getQueue(): Promise<DispatchQueueItem[]> {
      const items = await client.get<RawDispatchQueueItem[]>('/dispatch/queue');
      return items.map(toDispatchQueueItem);
    },

    async getClusters(): Promise<DispatchCluster[]> {
      const items = await client.get<RawDispatchCluster[]>('/dispatch/targets');
      return items.map(toDispatchCluster);
    },

    async approve(
      items: DispatchApprovalItem[],
      options: DispatchApprovalOptions = {},
    ): Promise<DispatchApprovalResult[]> {
      const results = await client.post<RawDispatchApprovalResult[]>('/dispatch/approve', {
        items: items.map((item) => ({
          saga_id: item.sagaId,
          issue_id: item.issueId,
          repo: item.repo,
          ...(item.instanceId || item.connectionId
            ? { connection_id: item.instanceId ?? item.connectionId }
            : {}),
          ...(item.workflowId ? { workflow_id: item.workflowId } : {}),
          ...(item.sessionDefinition ? { session_definition: item.sessionDefinition } : {}),
        })),
        ...(options.model ? { model: options.model } : {}),
        ...(options.systemPrompt ? { system_prompt: options.systemPrompt } : {}),
        ...(options.instanceId || options.connectionId
          ? { connection_id: options.instanceId ?? options.connectionId }
          : {}),
        ...(options.sessionDefinition ? { session_definition: options.sessionDefinition } : {}),
        ...(options.workloadType ? { workload_type: options.workloadType } : {}),
        ...(options.workloadConfig ? { workload_config: options.workloadConfig } : {}),
      });
      return results.map(toDispatchApprovalResult);
    },

    async dispatch(runId: string): Promise<void> {
      await client.post<void>(`/dispatch/${encodeURIComponent(runId)}`, {});
    },

    async dispatchBatch(runIds: string[]): Promise<DispatchResult> {
      return client.post<DispatchResult>('/dispatch/batch', { run_ids: runIds });
    },
  };
}

export function buildWorkflowHttpAdapter(client: ApiClient): IWorkflowService {
  return {
    async listWorkflows() {
      const raw = await client.get<RawWorkflow[]>('/workflows');
      return raw.map(toWorkflow);
    },

    async getWorkflow(id: string) {
      try {
        const raw = await client.get<RawWorkflow>(`/workflows/${encodeURIComponent(id)}`);
        return toWorkflow(raw);
      } catch {
        return null;
      }
    },

    async saveWorkflow(workflow: Workflow) {
      const body = toWorkflowBody(workflow);
      try {
        const existing = await client.get<RawWorkflow>(
          `/workflows/${encodeURIComponent(workflow.id)}`,
        );
        if (existing) {
          const raw = await client.put<RawWorkflow>(
            `/workflows/${encodeURIComponent(workflow.id)}`,
            body,
          );
          return toWorkflow(raw);
        }
      } catch {
        // Fall through to create when the workflow doesn't exist yet.
      }

      const raw = await client.post<RawWorkflow>('/workflows', body);
      return toWorkflow(raw);
    },

    async deleteWorkflow(id: string) {
      await client.delete<void>(`/workflows/${encodeURIComponent(id)}`);
    },

    async launchWorkflow(workflowId: string, request: WorkflowLaunchRequest) {
      const raw = await client.post<RawWorkflowLaunchResult>(
        `/workflows/${encodeURIComponent(workflowId)}/launch`,
        toWorkflowLaunchBody(request),
      );
      return toWorkflowLaunchResult(raw);
    },
  };
}

// ---------------------------------------------------------------------------
// Settings raw types (snake_case)
// ---------------------------------------------------------------------------

interface RawRetryPolicy {
  max_retries: number;
  retry_delay_seconds: number;
  escalate_on_exhaustion: boolean;
}

interface RawFlockConfig {
  flock_name: string;
  default_base_branch: string;
  default_tracker_type: string;
  default_repos: string[];
  max_active_sagas: number;
  auto_create_milestones: boolean;
  updated_at: string;
}

interface RawDispatchDefaults {
  confidence_threshold: number;
  max_concurrent_runs: number;
  auto_continue: boolean;
  batch_size: number;
  retry_policy: RawRetryPolicy;
  quiet_hours?: string;
  escalate_after?: string;
  updated_at: string;
}

interface RawNotificationSettings {
  channel: string;
  on_run_pending_approval: boolean;
  on_run_merged: boolean;
  on_run_failed: boolean;
  on_saga_complete: boolean;
  on_dispatcher_error: boolean;
  webhook_url: string | null;
  updated_at: string;
}

interface RawAuditEntry {
  id: string;
  kind: string;
  summary: string;
  actor: string;
  payload: Record<string, unknown> | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Settings transforms
// ---------------------------------------------------------------------------

function toFlockConfig(raw: RawFlockConfig): FlockConfig {
  return {
    flockName: raw.flock_name,
    defaultBaseBranch: raw.default_base_branch,
    defaultTrackerType: raw.default_tracker_type,
    defaultRepos: raw.default_repos,
    maxActiveSagas: raw.max_active_sagas,
    autoCreateMilestones: raw.auto_create_milestones,
    updatedAt: raw.updated_at,
  };
}

function toDispatchDefaults(raw: RawDispatchDefaults): DispatchDefaults {
  return {
    confidenceThreshold: raw.confidence_threshold,
    maxConcurrentRuns: raw.max_concurrent_runs,
    autoContinue: raw.auto_continue,
    batchSize: raw.batch_size,
    retryPolicy: {
      maxRetries: raw.retry_policy.max_retries,
      retryDelaySeconds: raw.retry_policy.retry_delay_seconds,
      escalateOnExhaustion: raw.retry_policy.escalate_on_exhaustion,
    },
    quietHours: raw.quiet_hours ?? '22:00–07:00 UTC',
    escalateAfter: raw.escalate_after ?? '30m',
    updatedAt: raw.updated_at,
  };
}

function toNotificationSettings(raw: RawNotificationSettings): NotificationSettings {
  return {
    channel: raw.channel as NotificationSettings['channel'],
    onRunPendingApproval: raw.on_run_pending_approval,
    onRunMerged: raw.on_run_merged,
    onRunFailed: raw.on_run_failed,
    onSagaComplete: raw.on_saga_complete,
    onDispatcherError: raw.on_dispatcher_error,
    webhookUrl: raw.webhook_url,
    updatedAt: raw.updated_at,
  };
}

function toAuditEntry(raw: RawAuditEntry): AuditEntry {
  return {
    id: raw.id,
    kind: raw.kind as AuditEntry['kind'],
    summary: raw.summary,
    actor: raw.actor,
    payload: raw.payload,
    createdAt: raw.created_at,
  };
}

/**
 * Build an ITingSettingsService backed by the Ting settings API.
 */
export function buildTingSettingsHttpAdapter(client: ApiClient): ITingSettingsService {
  return {
    async getFlockConfig() {
      const raw = await client.get<RawFlockConfig>('/settings/flock');
      return toFlockConfig(raw);
    },

    async updateFlockConfig(patch) {
      const body: Record<string, unknown> = {};
      if (patch.flockName !== undefined) body['flock_name'] = patch.flockName;
      if (patch.defaultBaseBranch !== undefined)
        body['default_base_branch'] = patch.defaultBaseBranch;
      if (patch.defaultTrackerType !== undefined)
        body['default_tracker_type'] = patch.defaultTrackerType;
      if (patch.defaultRepos !== undefined) body['default_repos'] = patch.defaultRepos;
      if (patch.maxActiveSagas !== undefined) body['max_active_sagas'] = patch.maxActiveSagas;
      if (patch.autoCreateMilestones !== undefined)
        body['auto_create_milestones'] = patch.autoCreateMilestones;
      const raw = await client.patch<RawFlockConfig>('/settings/flock', body);
      return toFlockConfig(raw);
    },

    async getDispatchDefaults() {
      const raw = await client.get<RawDispatchDefaults>('/settings/dispatch');
      return toDispatchDefaults(raw);
    },

    async updateDispatchDefaults(patch) {
      const body: Record<string, unknown> = {};
      if (patch.confidenceThreshold !== undefined)
        body['confidence_threshold'] = patch.confidenceThreshold;
      if (patch.maxConcurrentRuns !== undefined)
        body['max_concurrent_runs'] = patch.maxConcurrentRuns;
      if (patch.autoContinue !== undefined) body['auto_continue'] = patch.autoContinue;
      if (patch.batchSize !== undefined) body['batch_size'] = patch.batchSize;
      if (patch.retryPolicy !== undefined) {
        body['retry_policy'] = {
          max_retries: patch.retryPolicy.maxRetries,
          retry_delay_seconds: patch.retryPolicy.retryDelaySeconds,
          escalate_on_exhaustion: patch.retryPolicy.escalateOnExhaustion,
        };
      }
      if (patch.quietHours !== undefined) body['quiet_hours'] = patch.quietHours;
      if (patch.escalateAfter !== undefined) body['escalate_after'] = patch.escalateAfter;
      const raw = await client.patch<RawDispatchDefaults>('/settings/dispatch', body);
      return toDispatchDefaults(raw);
    },

    async getNotificationSettings() {
      const raw = await client.get<RawNotificationSettings>('/settings/notifications');
      return toNotificationSettings(raw);
    },

    async updateNotificationSettings(patch) {
      const body: Record<string, unknown> = {};
      if (patch.channel !== undefined) body['channel'] = patch.channel;
      if (patch.onRunPendingApproval !== undefined)
        body['on_run_pending_approval'] = patch.onRunPendingApproval;
      if (patch.onRunMerged !== undefined) body['on_run_merged'] = patch.onRunMerged;
      if (patch.onRunFailed !== undefined) body['on_run_failed'] = patch.onRunFailed;
      if (patch.onSagaComplete !== undefined) body['on_saga_complete'] = patch.onSagaComplete;
      if (patch.onDispatcherError !== undefined)
        body['on_dispatcher_error'] = patch.onDispatcherError;
      if (patch.webhookUrl !== undefined) body['webhook_url'] = patch.webhookUrl;
      const raw = await client.patch<RawNotificationSettings>('/settings/notifications', body);
      return toNotificationSettings(raw);
    },
  };
}

/**
 * Build an IAuditLogService backed by the Ting audit API.
 */
export function buildTingAuditLogHttpAdapter(client: ApiClient): IAuditLogService {
  return {
    async listAuditEntries(filter?: AuditFilter) {
      const params = new URLSearchParams();
      if (filter?.kinds) params.set('kinds', filter.kinds.join(','));
      if (filter?.actor) params.set('actor', filter.actor);
      if (filter?.since) params.set('since', filter.since);
      if (filter?.until) params.set('until', filter.until);
      if (filter?.limit) params.set('limit', String(filter.limit));
      const query = params.toString() ? `?${params.toString()}` : '';
      const raw = await client.get<RawAuditEntry[]>(`/audit${query}`);
      return raw.map(toAuditEntry);
    },
  };
}
