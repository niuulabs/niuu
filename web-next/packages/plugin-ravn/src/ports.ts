/**
 * Ravn plugin port interfaces + persona API types.
 *
 * All types in this file are either pure TypeScript interfaces (no runtime
 * cost) or type aliases. Implementations live in src/adapters/.
 */

import type { BudgetState, PersonaRole, FieldType } from '@niuulabs/domain';
import type { Ravn, ResidentDeploymentProfile } from './domain/ravn';
import type { Session } from './domain/session';
import type { Trigger } from './domain/trigger';
import type { Message } from './domain/message';

// ---------------------------------------------------------------------------
// Persona API types — migrated from web/src/modules/ravn/api/types.ts
// (camelCase client representation of the snake_case server responses)
// ---------------------------------------------------------------------------

export interface PersonaLLM {
  thinkingEnabled: boolean;
  maxTokens: number;
  temperature?: number;
}

export interface PersonaProduces {
  eventType: string;
  schemaDef: Record<string, FieldType>;
}

export interface PersonaConsumesEvent {
  name: string;
  injects?: string[];
  trust?: number;
}

export interface PersonaConsumes {
  events: PersonaConsumesEvent[];
  schemaDef: Record<string, FieldType>;
}

export interface PersonaFanIn {
  strategy: string;
  params: Record<string, unknown>;
}

export interface PersonaSummary {
  name: string;
  role: PersonaRole;
  letter: string;
  color: string;
  summary: string;
  permissionMode: string;
  allowedTools: string[];
  iterationBudget: number;
  isBuiltin: boolean;
  hasOverride: boolean;
  producesEvent: string;
  consumesEvents: string[];
}

export interface PersonaDetail extends PersonaSummary {
  description: string;
  systemPromptTemplate: string;
  forbiddenTools: string[];
  llm: PersonaLLM;
  produces: PersonaProduces;
  consumes: PersonaConsumes;
  fanIn?: PersonaFanIn;
  mimirWriteRouting?: 'local' | 'shared' | 'domain';
  yamlSource: string;
  overrideSource?: string;
}

export interface PersonaCreateRequest {
  name: string;
  role: PersonaRole;
  letter: string;
  color: string;
  summary: string;
  description: string;
  systemPromptTemplate: string;
  allowedTools: string[];
  forbiddenTools: string[];
  permissionMode: string;
  iterationBudget: number;
  llmThinkingEnabled: boolean;
  llmMaxTokens: number;
  llmTemperature?: number;
  producesEventType: string;
  producesSchema: Record<string, FieldType>;
  consumesEvents: PersonaConsumesEvent[];
  consumesSchema: Record<string, FieldType>;
  fanInStrategy?: string;
  fanInParams?: Record<string, unknown>;
  mimirWriteRouting?: 'local' | 'shared' | 'domain';
}

export interface PersonaForkRequest {
  newName: string;
}

export type PersonaFilter = 'all' | 'builtin' | 'custom';

// ---------------------------------------------------------------------------
// Port interfaces
// ---------------------------------------------------------------------------

/** CRUD store for Persona configurations. */
export interface IPersonaStore {
  listPersonas(filter?: PersonaFilter): Promise<PersonaSummary[]>;
  getPersona(name: string): Promise<PersonaDetail>;
  getPersonaYaml(name: string): Promise<string>;
  createPersona(req: PersonaCreateRequest): Promise<PersonaDetail>;
  updatePersona(name: string, req: PersonaCreateRequest): Promise<PersonaDetail>;
  deletePersona(name: string): Promise<void>;
  forkPersona(name: string, req: PersonaForkRequest): Promise<PersonaDetail>;
}

/** Read stream for Ravn fleet state. */
export interface IRavenStream {
  listRavens(): Promise<Ravn[]>;
  getRaven(id: string): Promise<Ravn>;
}

export type ResidentLifecycleAction = 'restart' | 'suspend' | 'resume';

export interface DeployResidentRequest {
  name: string;
  profileId: string;
  instanceId: string;
  personaName?: string;
  model?: string;
}

export interface CreateResidentSessionRequest {
  title: string;
  model?: string;
}

export interface ResidentLogEntry {
  timestampMs: number;
  level: string;
  source: string;
  target: string;
  message: string;
  fields: Record<string, string>;
}

export interface ResidentLogPage {
  entries: ResidentLogEntry[];
  bufferTotal: number;
}

/** Commands and backend-aware reads for managed resident runtimes. */
export interface IResidentControl {
  listProfiles(): Promise<ResidentDeploymentProfile[]>;
  deploy(request: DeployResidentRequest): Promise<Ravn>;
  applyLifecycle(ravn: Ravn, action: ResidentLifecycleAction): Promise<Ravn>;
  delete(ravn: Ravn): Promise<void>;
  getLogs(ravn: Ravn): Promise<ResidentLogPage>;
  listSessions(ravn: Ravn): Promise<Session[]>;
  createSession(ravn: Ravn, request: CreateResidentSessionRequest): Promise<Session>;
  deleteSession(ravn: Ravn, sessionId: string): Promise<void>;
}

/** Read stream for Session transcripts. */
export interface ISessionStream {
  listSessions(): Promise<Session[]>;
  getSession(id: string, instanceId?: string): Promise<Session>;
  getMessages(sessionId: string): Promise<Message[]>;
}

/** CRUD store for Triggers. */
export interface ITriggerStore {
  listTriggers(): Promise<Trigger[]>;
  createTrigger(t: Omit<Trigger, 'id' | 'createdAt'>): Promise<Trigger>;
  deleteTrigger(id: string): Promise<void>;
}

/** Read stream for Budget state. */
export interface IBudgetStream {
  getBudget(ravnId: string): Promise<BudgetState>;
  getFleetBudget(): Promise<BudgetState>;
}

export interface WardenFeatures {
  wakefulnessEnabled: boolean;
  dreamCycleEnabled: boolean;
  threadQueueEnabled: boolean;
  threadEnricherEnabled: boolean;
  recapEnabled: boolean;
  sourceTriggerEnabled: boolean;
  stalenessTriggerEnabled: boolean;
}

export interface WardenScheduleConfig {
  dreamCycleCronExpression: string;
  dreamCyclePollIntervalSeconds: number;
  sourceTriggerPollIntervalSeconds: number;
  stalenessTriggerScheduleHours: number;
}

export interface WardenConsole {
  enabled: boolean;
  host: string;
  port: number;
  publicHost?: string;
  authMode: 'noop' | 'token';
}

export interface WardenDreamSummary {
  id: string;
  timestamp: string;
  ravn: string;
  mounts: string[];
  pagesUpdated: number;
  entitiesCreated: number;
  lintFixes: number;
  durationMs: number;
}

export interface WardenRuntime {
  state?: 'active' | 'idle' | 'offline';
  pagesTouched?: number;
  lastStartedAt?: string;
  lastDream?: WardenDreamSummary | null;
}

export interface WardenSupervisor {
  installed: boolean;
  serviceLabel?: string;
  serviceFile?: string;
  configFile?: string;
  startCommand?: string;
  stdoutLog?: string;
  stderrLog?: string;
  lastInstallAt?: string;
  observation?: WardenObservation;
}

export interface WardenObservedField {
  label: string;
  value: string;
}

export interface WardenObservation {
  status: 'running' | 'idle' | 'missing' | 'degraded' | 'unknown';
  detail?: string;
  source?: string;
  checkedAt?: string;
  fields?: WardenObservedField[];
}

export interface WardenOperator {
  rune?: string;
  bio?: string;
  expertise?: string[];
  tools?: string[];
  role?: PersonaRole;
}

export interface WardenSummary {
  id: string;
  name: string;
  persona: string;
  profile: string;
  model: string;
  deployment: string;
  deploymentKwargs?: Record<string, unknown>;
  mountNames: string[];
  writeMount: string;
  readMountNames: string[];
  writeMountNames: string[];
  categoryScope: string[];
  features: WardenFeatures;
  schedules: WardenScheduleConfig;
  console: WardenConsole;
  autostart: boolean;
  createdAt: string;
  createdBy: string;
  runtime?: WardenRuntime;
  supervisor?: WardenSupervisor;
  operator?: WardenOperator;
}

export interface WardenLogEntry {
  id: string;
  source: 'stdout' | 'stderr' | string;
  lineNumber: number;
  raw: string;
  timestamp?: string;
  level?: string;
  logger?: string;
  message: string;
}

export type WardenListener = (warden: WardenSummary) => void;

export interface WardenCreateRequest {
  name: string;
  persona?: string;
  profile?: string;
  model?: string;
  deployment?: string;
  deploymentKwargs?: Record<string, unknown>;
  mountNames?: string[];
  writeMount?: string;
  readMountNames?: string[];
  writeMountNames?: string[];
  categoryScope?: string[];
  features?: Partial<WardenFeatures>;
  schedules?: Partial<WardenScheduleConfig>;
  console?: Partial<WardenConsole>;
  autostart?: boolean;
  createdBy?: string;
}

export interface IWardenStore {
  listWardens(): Promise<WardenSummary[]>;
  getWarden(id: string): Promise<WardenSummary>;
  createWarden(req: WardenCreateRequest): Promise<WardenSummary>;
  subscribeWarden(id: string, listener: WardenListener): () => void;
  observeWarden(id: string): Promise<WardenSummary>;
  installWarden(id: string): Promise<WardenSummary>;
  startWarden(id: string): Promise<WardenSummary>;
  stopWarden(id: string): Promise<WardenSummary>;
  uninstallWarden(id: string): Promise<WardenSummary>;
  getWardenLogs(
    id: string,
    options?: { stream?: 'stdout' | 'stderr'; limit?: number },
  ): Promise<WardenLogEntry[]>;
  getWardenActivity(id: string, limit?: number): Promise<WardenLogEntry[]>;
}
