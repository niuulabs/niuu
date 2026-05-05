/**
 * HTTP adapters for all Tyr ports.
 *
 * Adapted from web/src/modules/tyr/adapters/api/
 * Translates snake_case server responses to camelCase domain types.
 *
 * All factory functions accept an ApiClient structurally compatible with
 * @niuulabs/query (get / post / put / patch / delete methods).
 */

import type { ApiClient } from '@niuulabs/query';
import type {
  ITyrService,
  IDispatcherService,
  ITyrSessionService,
  ITrackerBrowserService,
  ITyrIntegrationService,
  IDispatchBus,
  DispatchResult,
  DispatchQueueItem,
  DispatchApprovalItem,
  DispatchApprovalOptions,
  DispatchApprovalResult,
  IWorkflowService,
  ITyrSettingsService,
  IAuditLogService,
  CommitSagaRequest,
  PlanSession,
  ExtractedStructure,
  PhaseSpec,
  IntegrationConnection,
  CreateIntegrationParams,
  ConnectionTestResult,
  TelegramSetupResult,
  FlockConfig,
  DispatchDefaults,
  NotificationSettings,
  AuditEntry,
  AuditFilter,
} from '../ports';
import type { Saga, Phase, Raid } from '../domain/saga';
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
  tracker_type: string;
  slug: string;
  name: string;
  repos: string[];
  feature_branch: string;
  base_branch?: string;
  status: string;
  confidence: number;
  created_at: string;
  workflow_id?: string | null;
  workflow?: string | null;
  workflow_version?: string | null;
  phase_summary: {
    total: number;
    completed: number;
  };
}

interface RawRaid {
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
  raids: RawRaid[];
}

interface RawDispatcherState {
  id: string;
  running: boolean;
  threshold: number;
  max_concurrent_raids: number;
  auto_continue: boolean;
  updated_at: string;
}

interface RawSessionInfo {
  session_id: string;
  status: string;
  chronicle_lines: string[];
  branch: string | null;
  confidence: number;
  raid_name: string;
  saga_name: string;
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

interface RawIntegrationConnection {
  id: string;
  integration_type: string;
  adapter: string;
  credential_name: string;
  enabled: boolean;
  status: string;
  created_at: string;
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
}

interface RawDispatchApprovalResult {
  issue_id: string;
  session_id: string;
  session_name: string;
  status: string;
  cluster_name: string;
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
  definition_yaml: string | null;
  compile_errors: string[];
}

// ---------------------------------------------------------------------------
// Transform functions
// ---------------------------------------------------------------------------

function toRaid(raw: RawRaid): Raid {
  return {
    id: raw.id,
    phaseId: raw.phase_id,
    trackerId: raw.tracker_id,
    name: raw.name,
    description: raw.description,
    acceptanceCriteria: raw.acceptance_criteria,
    declaredFiles: raw.declared_files,
    estimateHours: raw.estimate_hours,
    status: raw.status as Raid['status'],
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
    raids: raw.raids.map(toRaid),
  };
}

function toSaga(raw: RawSaga): Saga {
  return {
    id: raw.id,
    trackerId: raw.tracker_id,
    trackerType: raw.tracker_type,
    slug: raw.slug,
    name: raw.name,
    repos: raw.repos,
    featureBranch: raw.feature_branch,
    baseBranch: raw.base_branch ?? 'main',
    status: raw.status as Saga['status'],
    confidence: raw.confidence,
    createdAt: raw.created_at,
    workflowId: raw.workflow_id ?? undefined,
    workflow: raw.workflow ?? undefined,
    workflowVersion: raw.workflow_version ?? undefined,
    phaseSummary: {
      total: raw.phase_summary.total,
      completed: raw.phase_summary.completed,
    },
  };
}

function toDispatcherState(raw: RawDispatcherState): DispatcherState {
  return {
    id: raw.id,
    running: raw.running,
    threshold: raw.threshold,
    maxConcurrentRaids: raw.max_concurrent_raids,
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
    raidName: raw.raid_name,
    sagaName: raw.saga_name,
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

function toIntegrationConnection(raw: RawIntegrationConnection): IntegrationConnection {
  return {
    id: raw.id,
    integrationType: raw.integration_type,
    adapter: raw.adapter,
    credentialName: raw.credential_name,
    enabled: raw.enabled,
    status: raw.status,
    createdAt: raw.created_at,
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

function toCommitRequestBody(req: CommitSagaRequest): Record<string, unknown> {
  return {
    name: req.name,
    slug: req.slug,
    description: req.description,
    repos: req.repos,
    base_branch: req.baseBranch,
    phases: req.phases.map((p) => ({
      name: p.name,
      raids: p.raids.map((r) => ({
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
  return {
    id: raw.id,
    name: raw.name,
    description: raw.description || undefined,
    version: raw.version || undefined,
    scope: raw.scope,
    ownerId: raw.owner_id,
    definitionYaml: raw.definition_yaml,
    compileErrors: raw.compile_errors ?? [],
    nodes: raw.nodes,
    edges: raw.edges,
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
    definition_yaml: workflow.definitionYaml ?? null,
  };
}

// ---------------------------------------------------------------------------
// Factory functions
// ---------------------------------------------------------------------------

/**
 * Build an ITyrService backed by the Tyr REST API.
 *
 * @param client - HTTP client scoped to the Tyr sagas base path.
 */
export function buildTyrHttpAdapter(client: ApiClient): ITyrService {
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
  };
}

/**
 * Build an IDispatcherService backed by the Tyr dispatcher API.
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
  };
}

/**
 * Build an ITyrSessionService backed by the Tyr sessions API.
 *
 * @param client - HTTP client scoped to the sessions base path.
 */
export function buildTyrSessionHttpAdapter(client: ApiClient): ITyrSessionService {
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
 * Build an ITrackerBrowserService backed by the Tyr tracker API.
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

    async importProject(projectId: string, repos: string[], baseBranch?: string) {
      const raw = await client.post<RawSaga>('/tracker/import', {
        project_id: projectId,
        repos,
        base_branch: baseBranch,
      });
      return toSaga(raw);
    },
  };
}

/**
 * Build an ITyrIntegrationService backed by the Tyr integrations API.
 *
 * @param client - HTTP client scoped to the integrations base path.
 */
export function buildTyrIntegrationHttpAdapter(client: ApiClient): ITyrIntegrationService {
  return {
    async listIntegrations() {
      const raw = await client.get<RawIntegrationConnection[]>('/integrations');
      return raw.map(toIntegrationConnection);
    },

    async createIntegration(params: CreateIntegrationParams) {
      const raw = await client.post<RawIntegrationConnection>('/integrations', {
        integration_type: params.integrationType,
        adapter: params.adapter,
        credential_name: params.credentialName,
        credential_value: params.credentialValue,
        config: params.config,
      });
      return toIntegrationConnection(raw);
    },

    async deleteIntegration(id: string) {
      await client.delete<void>(`/integrations/${encodeURIComponent(id)}`);
    },

    async toggleIntegration(id: string, enabled: boolean) {
      const raw = await client.patch<RawIntegrationConnection>(
        `/integrations/${encodeURIComponent(id)}`,
        { enabled },
      );
      return toIntegrationConnection(raw);
    },

    async testConnection(id: string) {
      return client.post<ConnectionTestResult>(`/integrations/${encodeURIComponent(id)}/test`, {});
    },

    async getTelegramSetup() {
      return client.get<TelegramSetupResult>('/integrations/telegram/setup');
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

    async approve(
      items: DispatchApprovalItem[],
      options: DispatchApprovalOptions = {},
    ): Promise<DispatchApprovalResult[]> {
      const results = await client.post<RawDispatchApprovalResult[]>('/dispatch/approve', {
        items: items.map((item) => ({
          saga_id: item.sagaId,
          issue_id: item.issueId,
          repo: item.repo,
          ...(item.connectionId ? { connection_id: item.connectionId } : {}),
          ...(item.workflowId ? { workflow_id: item.workflowId } : {}),
          ...(item.sessionDefinition ? { session_definition: item.sessionDefinition } : {}),
        })),
        ...(options.model ? { model: options.model } : {}),
        ...(options.systemPrompt ? { system_prompt: options.systemPrompt } : {}),
        ...(options.connectionId ? { connection_id: options.connectionId } : {}),
        ...(options.sessionDefinition ? { session_definition: options.sessionDefinition } : {}),
        ...(options.workloadType ? { workload_type: options.workloadType } : {}),
        ...(options.workloadConfig ? { workload_config: options.workloadConfig } : {}),
      });
      return results.map(toDispatchApprovalResult);
    },

    async dispatch(raidId: string): Promise<void> {
      await client.post<void>(`/dispatch/${encodeURIComponent(raidId)}`, {});
    },

    async dispatchBatch(raidIds: string[]): Promise<DispatchResult> {
      return client.post<DispatchResult>('/dispatch/batch', { raid_ids: raidIds });
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
  max_concurrent_raids: number;
  auto_continue: boolean;
  batch_size: number;
  retry_policy: RawRetryPolicy;
  quiet_hours?: string;
  escalate_after?: string;
  updated_at: string;
}

interface RawNotificationSettings {
  channel: string;
  on_raid_pending_approval: boolean;
  on_raid_merged: boolean;
  on_raid_failed: boolean;
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
    maxConcurrentRaids: raw.max_concurrent_raids,
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
    onRaidPendingApproval: raw.on_raid_pending_approval,
    onRaidMerged: raw.on_raid_merged,
    onRaidFailed: raw.on_raid_failed,
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
 * Build an ITyrSettingsService backed by the Tyr settings API.
 */
export function buildTyrSettingsHttpAdapter(client: ApiClient): ITyrSettingsService {
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
      if (patch.maxConcurrentRaids !== undefined)
        body['max_concurrent_raids'] = patch.maxConcurrentRaids;
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
      if (patch.onRaidPendingApproval !== undefined)
        body['on_raid_pending_approval'] = patch.onRaidPendingApproval;
      if (patch.onRaidMerged !== undefined) body['on_raid_merged'] = patch.onRaidMerged;
      if (patch.onRaidFailed !== undefined) body['on_raid_failed'] = patch.onRaidFailed;
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
 * Build an IAuditLogService backed by the Tyr audit API.
 */
export function buildTyrAuditLogHttpAdapter(client: ApiClient): IAuditLogService {
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
