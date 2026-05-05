/**
 * Tyr plugin port interfaces.
 *
 * Migrated from web/src/modules/tyr/ports/ — shape preserved, imports updated.
 *
 * All types in this file are pure TypeScript interfaces or type aliases.
 * Implementations live in src/adapters/.
 */

import type { Saga, Phase } from './domain/saga';
import type { DispatcherState } from './domain/dispatcher';
import type { SessionInfo } from './domain/session';
import type { TrackerProject, TrackerMilestone, TrackerIssue } from './domain/tracker';
import type { Workflow } from './domain/workflow';
import type {
  FlockConfig,
  DispatchDefaults,
  NotificationSettings,
  AuditEntry,
  AuditFilter,
} from './domain/settings';
import type { ClarifyingQuestion } from './domain/plan';

// Re-export domain types so consumers can import from a single location.
export type { Saga, Phase } from './domain/saga';
export type { DispatcherState, DispatchRule } from './domain/dispatcher';
export type { SessionInfo, TyrSessionStatus } from './domain/session';
export type { TrackerProject, TrackerMilestone, TrackerIssue, RepoInfo } from './domain/tracker';
export type { Workflow } from './domain/workflow';
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
// ITyrService — saga lifecycle and planning
// ---------------------------------------------------------------------------

export interface CommitSagaRequest {
  name: string;
  slug: string;
  description: string;
  repos: string[];
  baseBranch: string;
  phases: {
    name: string;
    raids: {
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
  /** Clarifying questions from the planning raven. Empty when the backend omits them. */
  questions: ClarifyingQuestion[];
}

export interface RaidSpec {
  name: string;
  description: string;
  acceptanceCriteria: string[];
  declaredFiles: string[];
  estimateHours: number;
  confidence: number;
  /** Size classification returned by the planning raven. */
  size?: 'S' | 'M' | 'L';
  /** Persona ID responsible for this raid (e.g. "coding-agent"). */
  persona?: string;
  /** Phase label this raid belongs to (e.g. "Build", "Verify"). */
  phase?: string;
}

export interface PhaseSpec {
  name: string;
  raids: RaidSpec[];
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

/**
 * Core Tyr service — saga lifecycle, planning, and decomposition.
 *
 * Lifted verbatim from web/src/modules/tyr/ports/tyr.port.ts; camelCase
 * field names replace snake_case in the request types.
 */
export interface ITyrService {
  getSagas(): Promise<Saga[]>;
  getSaga(id: string): Promise<Saga | null>;
  getPhases(sagaId: string): Promise<Phase[]>;
  createSaga(spec: string, repo: string): Promise<Saga>;
  commitSaga(request: CommitSagaRequest): Promise<Saga>;
  decompose(spec: string, repo: string): Promise<Phase[]>;
  spawnPlanSession(spec: string, repo: string): Promise<PlanSession>;
  extractStructure(text: string): Promise<ExtractedStructure>;
  assignWorkflow(sagaId: string, workflowId: string | null): Promise<Saga>;
}

// ---------------------------------------------------------------------------
// IDispatcherService — autonomous execution queue
// ---------------------------------------------------------------------------

/**
 * Dispatcher control service.
 *
 * Lifted from web/src/modules/tyr/ports/dispatcher.port.ts.
 */
export interface IDispatcherService {
  getState(): Promise<DispatcherState | null>;
  setRunning(running: boolean): Promise<void>;
  setThreshold(threshold: number): Promise<void>;
  setAutoContinue(autoContinue: boolean): Promise<void>;
  getLog(): Promise<string[]>;
}

// ---------------------------------------------------------------------------
// ITyrSessionService — raid session approval flow
// ---------------------------------------------------------------------------

/**
 * Session management service.
 *
 * Lifted from web/src/modules/tyr/ports/session.port.ts.
 */
export interface ITyrSessionService {
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
 * Lifted from web/src/modules/tyr/ports/tracker.port.ts.
 */
export interface ITrackerBrowserService {
  listProjects(): Promise<TrackerProject[]>;
  getProject(projectId: string): Promise<TrackerProject>;
  listMilestones(projectId: string): Promise<TrackerMilestone[]>;
  listIssues(projectId: string, milestoneId?: string): Promise<TrackerIssue[]>;
  importProject(projectId: string, repos: string[], baseBranch?: string): Promise<Saga>;
}

// ---------------------------------------------------------------------------
// ITyrIntegrationService — external integration connections
// ---------------------------------------------------------------------------

export interface IntegrationConnection {
  id: string;
  integrationType: string;
  adapter: string;
  credentialName: string;
  enabled: boolean;
  status: string;
  createdAt: string;
}

export interface TelegramSetupResult {
  deeplink: string;
  token: string;
}

export interface CreateIntegrationParams {
  integrationType: string;
  adapter: string;
  credentialName: string;
  credentialValue: string;
  config: Record<string, string>;
}

export interface ConnectionTestResult {
  success: boolean;
  message: string;
}

/**
 * Integration connection management service.
 *
 * Lifted from web/src/modules/tyr/ports/integrations.port.ts.
 */
export interface ITyrIntegrationService {
  listIntegrations(): Promise<IntegrationConnection[]>;
  createIntegration(params: CreateIntegrationParams): Promise<IntegrationConnection>;
  deleteIntegration(id: string): Promise<void>;
  toggleIntegration(id: string, enabled: boolean): Promise<IntegrationConnection>;
  testConnection(id: string): Promise<ConnectionTestResult>;
  getTelegramSetup(): Promise<TelegramSetupResult>;
}

// ---------------------------------------------------------------------------
// IWorkflowService — workflow DAG CRUD
// ---------------------------------------------------------------------------

/**
 * Workflow management service — list, fetch, save, and delete Workflow DAGs.
 */
export interface IWorkflowService {
  listWorkflows(): Promise<Workflow[]>;
  getWorkflow(id: string): Promise<Workflow | null>;
  saveWorkflow(workflow: Workflow): Promise<Workflow>;
  deleteWorkflow(id: string): Promise<void>;
}

// ---------------------------------------------------------------------------
// IDispatchBus — Sleipnir emit adapter
// ---------------------------------------------------------------------------

export interface DispatchResult {
  /** IDs of raids that were successfully queued for execution. */
  dispatched: string[];
  /** Raids that could not be dispatched and why. */
  failed: { raidId: string; reason: string }[];
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
}

export interface DispatchApprovalItem {
  sagaId: string;
  issueId: string;
  repo: string;
  connectionId?: string;
  workflowId?: string;
  sessionDefinition?: string;
}

export interface DispatchApprovalOptions {
  model?: string;
  systemPrompt?: string;
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

/**
 * Sleipnir dispatch bus port.
 *
 * Emits raid dispatch events to the autonomous execution queue.
 * Implementations: RabbitMQ (production), in-memory mock (dev/test).
 */
export interface IDispatchBus {
  getQueue(): Promise<DispatchQueueItem[]>;
  approve(
    items: DispatchApprovalItem[],
    options?: DispatchApprovalOptions,
  ): Promise<DispatchApprovalResult[]>;
  dispatch(raidId: string): Promise<void>;
  dispatchBatch(raidIds: string[]): Promise<DispatchResult>;
}

// ---------------------------------------------------------------------------
// ITyrPersonaViewService — minimal persona read port (mirrors IPersonaStore)
// Tyr Settings uses this to browse Ravn personas without duplicating the port.
// The 'ravn.personas' service key is wired at the app level with Ravn's adapter.
// ---------------------------------------------------------------------------

/**
 * Minimal persona summary shape needed by the Tyr settings personas browser.
 * Shape is intentionally compatible with plugin-ravn's PersonaSummary.
 */
export interface TyrPersonaSummary {
  name: string;
  permissionMode: string;
  allowedTools: string[];
  iterationBudget: number;
  isBuiltin: boolean;
  hasOverride: boolean;
  producesEvent: string;
  consumesEvents: string[];
  /** LLM model identifier (e.g. 'sonnet-4.5'). */
  model?: string;
  /** Functional role — drives the avatar shape (plan, build, verify, …). */
  role?: string;
}

/**
 * Minimal persona detail shape used by the Tyr settings YAML editor.
 */
export interface TyrPersonaDetail extends TyrPersonaSummary {
  systemPromptTemplate: string;
  forbiddenTools: string[];
  yamlSource: string;
}

/**
 * Minimal read-only persona port used by Tyr Settings.
 * The consumer must wire 'ravn.personas' (or compatible) in ServicesProvider.
 */
export interface ITyrPersonaViewService {
  listPersonas(filter?: 'all' | 'builtin' | 'custom'): Promise<TyrPersonaSummary[]>;
  getPersonaYaml(name: string): Promise<string>;
}

// ---------------------------------------------------------------------------
// ITyrSettingsService — flock config, dispatch defaults, notification settings
// ---------------------------------------------------------------------------

/**
 * Settings service for Tyr — manages flock config, dispatch defaults,
 * and notification settings.
 */
export interface ITyrSettingsService {
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
