/**
 * Ting plugin port interfaces.
 *
 * Migrated from web/src/modules/ting/ports/ — shape preserved, imports updated.
 *
 * All types in this file are pure TypeScript interfaces or type aliases.
 * Implementations live in src/adapters/.
 */

import type { Saga, Phase, SagaRepoRef } from './domain/saga';
import type { DispatcherState } from './domain/dispatcher';
import type { SessionInfo } from './domain/session';
import type { TrackerProject, TrackerMilestone, TrackerIssue } from './domain/tracker';
import type { Workflow } from './domain/workflow';
import type {
  ResearchCampaign,
  ResearchCampaignDetail,
  CampaignArtifact,
  CampaignArtifactDetail,
} from './domain/research';
import type {
  FlockConfig,
  DispatchDefaults,
  NotificationSettings,
  AuditEntry,
  AuditFilter,
} from './domain/settings';
import type { ClarifyingQuestion } from './domain/plan';

// Re-export domain types so consumers can import from a single location.
export type { Saga, Phase, SagaRepoRef } from './domain/saga';
export type { DispatcherState, DispatchRule } from './domain/dispatcher';
export type { SessionInfo, TingSessionStatus } from './domain/session';
export type { TrackerProject, TrackerMilestone, TrackerIssue, RepoInfo } from './domain/tracker';
export type { Workflow } from './domain/workflow';
export type {
  ResearchCampaign,
  ResearchCampaignDetail,
  CampaignArtifact,
  CampaignArtifactDetail,
  CampaignArtifactSummary,
  CampaignStageState,
  ResearchCampaignStatus,
} from './domain/research';
export type SpecCampaign = ResearchCampaign;
export type SpecCampaignDetail = ResearchCampaignDetail;
export type {
  FlockConfig,
  DispatchDefaults,
  RetryPolicy,
  NotificationSettings,
  NotificationChannel,
  AuditEntry,
  AuditEntryKind,
  AuditFilter,
} from './domain/settings';
export type { ClarifyingQuestion } from './domain/plan';

// ---------------------------------------------------------------------------
// ITingService — saga lifecycle and planning
// ---------------------------------------------------------------------------

export interface CommitSagaRequest {
  name: string;
  slug: string;
  description: string;
  repos: string[];
  baseBranch: string;
  phases: {
    name: string;
    runs: {
      name: string;
      description: string;
      acceptanceCriteria: string[];
      declaredFiles: string[];
      estimateHours: number;
    }[];
  }[];
  transcript?: string;
}

export interface PlanSession {
  sessionId: string;
  chatEndpoint: string | null;
  name?: string | null;
  prompt?: string | null;
  repo?: string | null;
  campaignSlug?: string | null;
  workflowName?: string | null;
  status?: string | null;
  activeStageId?: string | null;
  updatedAt?: string | null;
  stageState?: {
    stageId: string;
    label: string;
    status: string;
    startedAt?: string | null;
    completedAt?: string | null;
    reason?: string | null;
  }[];
  /** Clarifying questions from the planning raven. Empty when the backend omits them. */
  questions: ClarifyingQuestion[];
}

export interface RunSpec {
  name: string;
  description: string;
  acceptanceCriteria: string[];
  declaredFiles: string[];
  estimateHours: number;
  confidence: number;
  /** Size classification returned by the planning raven. */
  size?: 'S' | 'M' | 'L';
  /** Persona ID responsible for this run (e.g. "coding-agent"). */
  persona?: string;
  /** Phase label this run belongs to (e.g. "Build", "Verify"). */
  phase?: string;
}

export interface PhaseSpec {
  name: string;
  runs: RunSpec[];
}

export interface PlanRisk {
  /** Short category label — e.g. "blast", "untested", "dependency". */
  kind: string;
  message: string;
}

export interface ExtractedStructure {
  found: boolean;
  structure: {
    name: string;
    phases: PhaseSpec[];
    /** Risks flagged by the planning raven. */
    risks?: PlanRisk[];
  } | null;
}

export interface RunHelpRequest {
  summary: string;
  reason: string;
  attempted: string[];
  recommendation?: string;
  context: Record<string, unknown>;
  targetPeerId?: string;
  persona?: string;
}

export interface RunSessionMessage {
  id: string;
  sessionId: string;
  content: string;
  sender: string;
  createdAt: string;
  kind: 'message' | 'help_request';
  helpRequest?: RunHelpRequest | null;
}

/**
 * Core Ting service — saga lifecycle, planning, and decomposition.
 *
 * Lifted verbatim from web/src/modules/ting/ports/ting.port.ts; camelCase
 * field names replace snake_case in the request types.
 */
export interface ITingService {
  getSagas(): Promise<Saga[]>;
  getSaga(id: string): Promise<Saga | null>;
  deleteSaga?(id: string): Promise<void>;
  getPhases(sagaId: string): Promise<Phase[]>;
  listRunMessages(runId: string): Promise<RunSessionMessage[]>;
  sendRunMessage(runId: string, content: string, targetPeerId?: string): Promise<RunSessionMessage>;
  createSaga(spec: string, repo: string): Promise<Saga>;
  commitSaga(request: CommitSagaRequest): Promise<Saga>;
  decompose(spec: string, repo: string): Promise<Phase[]>;
  spawnPlanSession(spec: string, repo: string): Promise<PlanSession>;
  listPlanSessions?(): Promise<PlanSession[]>;
  getPlanSession?(campaignSlug: string): Promise<PlanSession | null>;
  cancelPlanSession?(campaignSlug: string): Promise<void>;
  getPlanDraft?(campaignSlug: string): Promise<ExtractedStructure>;
  sendPlanFeedback?(
    campaignSlug: string,
    content: string,
    decision?: 'approve' | 'changes_requested',
  ): Promise<void>;
  extractStructure(text: string): Promise<ExtractedStructure>;
  assignWorkflow(sagaId: string, workflowId: string | null): Promise<Saga>;
  assignTarget(sagaId: string, target: SagaTargetSelection): Promise<Saga>;
  assignRepos(sagaId: string, repoRefs: SagaRepoRef[]): Promise<Saga>;
}

// ---------------------------------------------------------------------------
// IDispatcherService — autonomous execution queue
// ---------------------------------------------------------------------------

/**
 * Dispatcher control service.
 *
 * Lifted from web/src/modules/ting/ports/dispatcher.port.ts.
 */
export interface IDispatcherService {
  getState(): Promise<DispatcherState | null>;
  setRunning(running: boolean): Promise<void>;
  setThreshold(threshold: number): Promise<void>;
  setAutoContinue(autoContinue: boolean): Promise<void>;
  getLog(): Promise<string[]>;
  getActivityLog(limit?: number): Promise<DispatcherActivityEvent[]>;
}

export interface DispatcherActivityEvent {
  id: string;
  event: string;
  data: Record<string, unknown>;
  ownerId: string;
  timestamp: string;
}

// ---------------------------------------------------------------------------
// ITingSessionService — run session approval flow
// ---------------------------------------------------------------------------

/**
 * Session management service.
 *
 * Lifted from web/src/modules/ting/ports/session.port.ts.
 */
export interface ITingSessionService {
  getSessions(): Promise<SessionInfo[]>;
  getSession(id: string): Promise<SessionInfo | null>;
  approve(sessionId: string): Promise<void>;
}

// ---------------------------------------------------------------------------
// ITrackerBrowserService — external issue tracker browsing
// ---------------------------------------------------------------------------

/**
 * Tracker browser service.
 *
 * Lifted from web/src/modules/ting/ports/tracker.port.ts.
 */
export interface ITrackerBrowserService {
  listProjects(): Promise<TrackerProject[]>;
  getProject(projectId: string): Promise<TrackerProject>;
  listMilestones(projectId: string): Promise<TrackerMilestone[]>;
  listIssues(projectId: string, milestoneId?: string): Promise<TrackerIssue[]>;
  importProject(
    projectId: string,
    repos: string[],
    baseBranch?: string,
    instanceId?: string | null,
    options?: ImportProjectOptions,
  ): Promise<Saga>;
}

export interface ImportProjectOptions {
  repoRefs?: { repo: string; branch: string }[];
  target?: SagaTargetSelection;
}

export type SagaTargetSelection =
  | { mode: 'default' }
  | { mode: 'instance'; instanceId: string }
  | { mode: 'tags'; tags: string[]; match?: 'all' | 'any' };

// ---------------------------------------------------------------------------
// IWorkflowService — workflow DAG CRUD
// ---------------------------------------------------------------------------

/**
 * Workflow management service — list, fetch, save, and delete Workflow DAGs.
 */
export interface WorkflowLaunchRequest {
  prompt: string;
  sessionName?: string;
  repo?: string;
  branch?: string;
  connectionId?: string;
}

export interface WorkflowLaunchResult {
  workflowId: string;
  workflowName: string;
  slug: string;
  sessionId: string;
  sessionName: string;
  status: string;
  clusterName: string;
}

export interface IWorkflowService {
  listWorkflows(): Promise<Workflow[]>;
  getWorkflow(id: string): Promise<Workflow | null>;
  saveWorkflow(workflow: Workflow): Promise<Workflow>;
  deleteWorkflow(id: string): Promise<void>;
  launchWorkflow(workflowId: string, request: WorkflowLaunchRequest): Promise<WorkflowLaunchResult>;
}

export interface CreateResearchCampaignRequest {
  question: string;
  name?: string;
  workflowId?: string;
  repo?: string;
  branch?: string;
  mode?: string;
  audience?: string;
  deliverable?: string;
  success?: string;
  constraints?: string[];
  monitoringCadence?: string;
  connectionId?: string;
}

export interface UpdateResearchCampaignRequest {
  name?: string;
  status?: string;
  metadata?: Record<string, unknown>;
}

export interface IResearchService {
  listCampaigns(): Promise<ResearchCampaign[]>;
  getCampaign(slug: string): Promise<ResearchCampaignDetail | null>;
  createCampaign(request: CreateResearchCampaignRequest): Promise<ResearchCampaign>;
  updateCampaign(slug: string, request: UpdateResearchCampaignRequest): Promise<ResearchCampaign>;
  deleteCampaign(slug: string): Promise<void>;
  listArtifacts(slug: string): Promise<CampaignArtifact[]>;
  getArtifact(slug: string, path: string): Promise<CampaignArtifactDetail | null>;
}

export interface CreateSpecCampaignRequest {
  prompt: string;
  name?: string;
  workflowId?: string;
  repo?: string;
  repos?: string[];
  branch?: string;
  context?: string;
  connectionId?: string;
}

export interface ReviewSpecCampaignRequest {
  decision: 'approve' | 'changes_requested';
  notes?: string;
  gateId?: string;
  nodeId?: string;
}

export interface ISpecsService {
  listCampaigns(): Promise<SpecCampaign[]>;
  getCampaign(slug: string): Promise<SpecCampaignDetail | null>;
  createCampaign(request: CreateSpecCampaignRequest): Promise<SpecCampaign>;
  deleteCampaign(slug: string): Promise<void>;
  listArtifacts(slug: string): Promise<CampaignArtifact[]>;
  getArtifact(slug: string, path: string): Promise<CampaignArtifactDetail | null>;
  reviewCampaign(slug: string, request: ReviewSpecCampaignRequest): Promise<SpecCampaign>;
}

// ---------------------------------------------------------------------------
// IDispatchBus — Sleipnir emit adapter
// ---------------------------------------------------------------------------

export interface DispatchResult {
  /** IDs of runs that were successfully queued for execution. */
  dispatched: string[];
  /** Runs that could not be dispatched and why. */
  failed: { runId: string; reason: string }[];
}

export interface DispatchQueueItem {
  sagaId: string;
  sagaName: string;
  sagaSlug: string;
  repos: string[];
  featureBranch: string;
  phaseName: string;
  issueId: string;
  identifier: string;
  title: string;
  description: string;
  status: string;
  priority: number;
  priorityLabel: string;
  estimate: number | null;
  url: string;
  workflowId?: string;
  workflow?: string;
  workflowVersion?: string;
  instanceId?: string;
  targetTags?: string[];
  targetMatch?: 'all' | 'any';
}

export interface DispatchApprovalItem {
  sagaId: string;
  issueId: string;
  repo: string;
  instanceId?: string;
  connectionId?: string;
  workflowId?: string;
  sessionDefinition?: string;
}

export interface DispatchApprovalOptions {
  model?: string;
  systemPrompt?: string;
  instanceId?: string;
  connectionId?: string;
  sessionDefinition?: string;
  workloadType?: string;
  workloadConfig?: Record<string, unknown>;
}

export interface DispatchApprovalResult {
  issueId: string;
  sessionId: string;
  sessionName: string;
  status: string;
  clusterName: string;
}

export interface DispatchCluster {
  instanceId?: string;
  connectionId: string;
  name: string;
  url: string;
  enabled: boolean;
  tags?: string[];
}

/**
 * Sleipnir dispatch bus port.
 *
 * Emits run dispatch events to the autonomous execution queue.
 * Implementations: RabbitMQ (production), in-memory mock (dev/test).
 */
export interface IDispatchBus {
  getQueue(): Promise<DispatchQueueItem[]>;
  getClusters(): Promise<DispatchCluster[]>;
  approve(
    items: DispatchApprovalItem[],
    options?: DispatchApprovalOptions,
  ): Promise<DispatchApprovalResult[]>;
  dispatch(runId: string): Promise<void>;
  dispatchBatch(runIds: string[]): Promise<DispatchResult>;
}

// ---------------------------------------------------------------------------
// ITingPersonaViewService — minimal persona read port (mirrors IPersonaStore)
// Ting Settings uses this to browse Ravn personas without duplicating the port.
// The 'ravn.personas' service key is wired at the app level with Ravn's adapter.
// ---------------------------------------------------------------------------

/**
 * Minimal persona summary shape needed by the Ting settings personas browser.
 * Shape is intentionally compatible with plugin-ravn's PersonaSummary.
 */
export interface TingPersonaSummary {
  name: string;
  permissionMode: string;
  allowedTools: string[];
  iterationBudget: number;
  isBuiltin: boolean;
  hasOverride: boolean;
  producesEvent: string;
  consumesEvents: string[];
  /** Functional role — drives the avatar shape (plan, build, verify, …). */
  role?: string;
}

/**
 * Minimal persona detail shape used by the Ting settings YAML editor.
 */
export interface TingPersonaDetail extends TingPersonaSummary {
  systemPromptTemplate: string;
  forbiddenTools: string[];
  yamlSource: string;
}

/**
 * Minimal read-only persona port used by Ting Settings.
 * The consumer must wire 'ravn.personas' (or compatible) in ServicesProvider.
 */
export interface ITingPersonaViewService {
  listPersonas(filter?: 'all' | 'builtin' | 'custom'): Promise<TingPersonaSummary[]>;
  getPersonaYaml(name: string): Promise<string>;
}

// ---------------------------------------------------------------------------
// ITingSettingsService — flock config, dispatch defaults, notification settings
// ---------------------------------------------------------------------------

/**
 * Settings service for Ting — manages flock config, dispatch defaults,
 * and notification settings.
 */
export interface ITingSettingsService {
  getFlockConfig(): Promise<FlockConfig>;
  updateFlockConfig(patch: Partial<Omit<FlockConfig, 'updatedAt'>>): Promise<FlockConfig>;
  getDispatchDefaults(): Promise<DispatchDefaults>;
  updateDispatchDefaults(
    patch: Partial<Omit<DispatchDefaults, 'updatedAt'>>,
  ): Promise<DispatchDefaults>;
  getNotificationSettings(): Promise<NotificationSettings>;
  updateNotificationSettings(
    patch: Partial<Omit<NotificationSettings, 'updatedAt'>>,
  ): Promise<NotificationSettings>;
}

// ---------------------------------------------------------------------------
// IAuditLogService — immutable audit trail for settings changes + dispatch events
// ---------------------------------------------------------------------------

/**
 * Read-only audit log service.
 */
export interface IAuditLogService {
  listAuditEntries(filter?: AuditFilter): Promise<AuditEntry[]>;
}
