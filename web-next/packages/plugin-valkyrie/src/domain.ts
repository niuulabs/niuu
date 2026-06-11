export type EnvironmentKind = 'kubernetes' | 'host' | 'printer' | 'generic';
export type EnvironmentHealth = 'healthy' | 'watch' | 'degraded' | 'critical';
export type WakefulnessState = 'sleeping' | 'watching' | 'wakeful' | 'dreaming';
export type AutonomyMode = 'guarded' | 'autonomous' | 'yolo';
export type SignalSeverity = 'info' | 'notice' | 'warning' | 'critical';
export type SignalStatus = 'new' | 'triaged' | 'acting' | 'resolved' | 'ignored';
export type ActionStatus = 'planned' | 'running' | 'succeeded' | 'failed' | 'rolled_back';
export type LearningScope = 'private' | 'environment' | 'domain' | 'flock' | 'shared';
export type LearningStatus =
  | 'requested'
  | 'candidate'
  | 'canary'
  | 'adopted'
  | 'rejected'
  | 'rolled_back'
  | 'completed';

export interface EnvironmentSummary {
  id: string;
  name: string;
  kind: EnvironmentKind;
  health: EnvironmentHealth;
  identitySource?: 'configured' | 'observed';
  flockId?: string;
  topologyNodeIds: string[];
  signalCount: number;
  unresolvedSignalCount: number;
  wakefulCount: number;
  dreamingCount: number;
  lastSignalAt: string;
}

export interface ValkyrieResident {
  id: string;
  name: string;
  environmentId: string;
  flockId?: string;
  persona: string;
  specialty: string;
  wakefulness: WakefulnessState;
  autonomyMode: AutonomyMode;
  status: 'online' | 'busy' | 'blocked' | 'offline';
  confidence: number;
  inboxSubjects: string[];
  toolCount: number;
  lastDreamAt?: string;
  lastActionAt?: string;
  lastObservedAt?: string;
  identitySource?: 'configured' | 'observed';
}

export interface EnvironmentSignal {
  id: string;
  environmentId: string;
  source: string;
  subject: string;
  summary: string;
  severity: SignalSeverity;
  status: SignalStatus;
  receivedAt: string;
  assignedValkyrieId?: string;
  labels: string[];
}

export interface OperationalState {
  id: string;
  environmentId: string;
  name: string;
  desired: string;
  observed: string;
  drift: 'none' | 'minor' | 'major';
  maintainedBy: string[];
  updatedAt: string;
}

export interface JudgmentRecord {
  id: string;
  environmentId: string;
  signalId: string;
  valkyrieId: string;
  verdict: 'ignore' | 'observe' | 'act' | 'escalate';
  confidence: number;
  rationale: string;
  createdAt: string;
}

export interface CourtDecision {
  id: string;
  environmentId: string;
  title: string;
  status: 'pending' | 'approved' | 'rejected' | 'executed';
  risk: 'low' | 'medium' | 'high' | 'hard_gate';
  decidedBy: string[];
  createdAt: string;
}

export interface ActionRecord {
  id: string;
  environmentId: string;
  title: string;
  status: ActionStatus;
  risk: 'low' | 'medium' | 'high';
  ownerValkyrieId: string;
  startedAt?: string;
  finishedAt?: string;
}

export interface HuddleMessage {
  id: string;
  huddleId: string;
  authorId: string;
  authorName: string;
  body: string;
  createdAt: string;
  directedTo?: string[];
}

export interface HuddleSummary {
  id: string;
  environmentId: string;
  targetFlockId?: string;
  title: string;
  status: 'open' | 'quiet' | 'closed';
  participantIds: string[];
  joined: boolean;
  joinedParticipantId?: string;
  joinedDisplayName?: string;
  joinedAction?: string;
  messages: HuddleMessage[];
  lastActivityAt: string;
}

export interface LearningRecord {
  id: string;
  title: string;
  summary: string;
  scope: LearningScope;
  status: LearningStatus;
  sourceEnvironmentId: string;
  sourceValkyrieId: string;
  targetFlockId?: string;
  confidence: number;
  evaluation: string;
  negativeTransferRisk: 'low' | 'medium' | 'high';
  redaction: 'none' | 'partial' | 'required';
  promotedTool?: string;
  createdAt: string;
  active?: boolean;
  currentScope?: LearningScope;
  targetScope?: LearningScope;
  availableScopes?: LearningScope[];
  artifactContent?: string;
  artifactPath?: string;
  artifactType?: string;
  sourceSignalIds?: string[];
  sourceEvidence?: Record<string, unknown>;
  dreamRationale?: string;
  odinReview?: {
    outcome?: string;
    approved?: boolean;
    rationale?: string;
    reviewer?: string;
    findings?: string[];
    requiredForActivation?: boolean;
  };
  history?: Array<{
    eventType: string;
    status: string;
    summary: string;
    observedAt: string;
    operatorId?: string;
    reason?: string;
  }>;
  commandDelivery?: {
    published?: boolean;
    eventType?: string;
    eventId?: string;
    message?: string;
    observedAt?: string;
  };
  canaryEnvironmentId?: string;
  override?: boolean;
}

export interface FlockSummary {
  id: string;
  name: string;
  domain: string;
  natsSubject: string;
  environmentIds: string[];
  valkyrieIds: string[];
  learningIds: string[];
  health: EnvironmentHealth;
  lastExchangeAt: string;
}

export interface FlockTransportStatus {
  id: string;
  label: string;
  environmentId?: string;
  account: string;
  streamName: string;
  subjectPrefix: string;
  messageCount: number;
  signalCount: number;
  activityCount: number;
  judgmentCount: number;
  actionCount: number;
  rejectedCount: number;
  consumerFilterSubjects: string[];
  health: EnvironmentHealth;
  lastMessageAt?: string;
  notes: string[];
}

export interface FlockLiveReport {
  title: string;
  status: EnvironmentHealth;
  lastObservedAt: string;
  totalMessages: number;
  sharedStream: string;
  routeSubject: string;
  projectionMode: 'local' | 'flock' | 'mixed';
  transports: FlockTransportStatus[];
  findings: string[];
}

export interface ValkyrieTelemetryTotals {
  eventsObserved: number;
  rawSignalEvents: number;
  logEvents?: number;
  pollsCompleted: number;
  pollFailures: number;
  signalsCollected: number;
  signalsPublished: number;
  duplicateSignals: number;
  tasksEnqueued: number;
  tasksStarted: number;
  tasksCompleted: number;
  tasksFailed: number;
  tasksDropped: number;
  judgments: number;
  actions: number;
  learningEvents: number;
  dreamCyclesStarted: number;
  dreamCyclesCompleted: number;
  dreamCyclesFailed: number;
  dreamCyclesNoop?: number;
  flockMessages: number;
  llmCalls?: number;
  llmTokens?: number;
  budgetDrops?: number;
  wakefulnessChanges?: number;
  toolRequests?: number;
  skillProposals?: number;
}

export interface ValkyrieEnvironmentTelemetry {
  environmentId: string;
  lastObservedAt: string;
  pollsCompleted: number;
  pollFailures: number;
  signalsCollected: number;
  signalsPublished: number;
  duplicateSignals: number;
  tasksEnqueued: number;
  tasksStarted: number;
  tasksCompleted: number;
  tasksFailed: number;
  tasksDropped: number;
  judgments: number;
  actions: number;
  learningEvents: number;
  dreamCycles: number;
}

export interface ValkyriePollTelemetry {
  environmentId: string;
  sourceId: string;
  status: 'completed' | 'failed';
  collected?: number;
  published?: number;
  duplicates?: number;
  tasksEnqueued?: number;
  durationMs?: number;
  error?: string;
  observedAt: string;
}

export interface ValkyrieTaskTelemetry {
  environmentId: string;
  taskId: string;
  title: string;
  status: 'started' | 'completed' | 'failed' | 'dropped';
  outcome?: string;
  reason?: string;
  triggeredBy?: string;
  persona?: string;
  observedAt: string;
}

export interface ValkyrieOutcomeTelemetry {
  environmentId: string;
  type: 'judgment' | 'action';
  eventType: string;
  taskId: string;
  valkyrieId?: string;
  verdict?: string;
  tier?: string;
  confidence?: number;
  recommendedAction?: string;
  summary?: string;
  valid?: boolean;
  observedAt: string;
}

export interface ValkyrieEventTelemetry {
  id: string;
  eventType: string;
  kind:
    | 'signal'
    | 'judgment'
    | 'action'
    | 'learning'
    | 'task'
    | 'runtime'
    | 'wakefulness'
    | 'presence'
    | 'flock'
    | 'log'
    | 'llm'
    | 'tool'
    | 'event';
  environmentId: string;
  valkyrieId?: string;
  valkyrieName?: string;
  source?: string;
  summary: string;
  urgency?: number;
  observedAt: string;
  correlationId?: string;
  details?: Record<string, unknown>;
}

export interface ValkyrieLogTelemetry {
  id: string;
  eventType: string;
  environmentId: string;
  valkyrieId?: string;
  valkyrieName?: string;
  level: string;
  component: string;
  message: string;
  taskId?: string;
  observedAt: string;
}

export interface ValkyrieLearningTelemetry {
  id: string;
  eventType: string;
  environmentId: string;
  valkyrieId?: string;
  dreamId?: string;
  title: string;
  status: string;
  artifactType?: string;
  riskClass?: string;
  policyDecision?: string;
  proposalsCreated?: number;
  proposalsApplied?: number;
  proposalsDeferred?: number;
  observedAt: string;
  summary?: string;
}

export interface ValkyrieToolNeedTelemetry {
  id: string;
  eventType: string;
  environmentId: string;
  valkyrieId?: string;
  taskId?: string;
  capability: string;
  status: string;
  summary: string;
  observedAt: string;
}

export interface ValkyrieRuntimeTelemetry {
  environmentId: string;
  valkyrieId: string;
  valkyrieName?: string;
  residentPersonality?: string;
  sourceCount: number;
  driveLoopEnabled: boolean;
  initiativeEnabled: boolean;
  pollIntervalSeconds: number;
  observedAt: string;
}

export interface ValkyrieLlmTelemetry {
  status: 'unknown' | 'configured' | 'healthy' | 'degraded' | 'failed';
  model: string;
  reflectionModel: string;
  postSessionReflectionEnabled: boolean;
  lastObservedAt: string;
}

export interface ValkyrieTelemetry {
  source: 'demo_projection' | 'sleipnir_events' | string;
  verified: boolean;
  lastObservedAt: string;
  totals: ValkyrieTelemetryTotals;
  byEnvironment: ValkyrieEnvironmentTelemetry[];
  recentPolls: ValkyriePollTelemetry[];
  recentTasks: ValkyrieTaskTelemetry[];
  recentOutcomes: ValkyrieOutcomeTelemetry[];
  recentEvents?: ValkyrieEventTelemetry[];
  recentLogs?: ValkyrieLogTelemetry[];
  recentLearning?: ValkyrieLearningTelemetry[];
  recentToolNeeds?: ValkyrieToolNeedTelemetry[];
  runtime: ValkyrieRuntimeTelemetry[];
  llm: ValkyrieLlmTelemetry;
  gaps: string[];
}

export interface ValkyrieDashboard {
  environments: EnvironmentSummary[];
  valkyries: ValkyrieResident[];
  flocks: FlockSummary[];
  signals: EnvironmentSignal[];
  operationalStates: OperationalState[];
  judgments: JudgmentRecord[];
  courtDecisions: CourtDecision[];
  actions: ActionRecord[];
  huddles: HuddleSummary[];
  learnings: LearningRecord[];
  liveReport?: FlockLiveReport;
  telemetry?: ValkyrieTelemetry;
  updatedAt: string;
}

export interface ValkyrieSignalEvent {
  type: 'signal' | 'judgment' | 'action' | 'learning' | 'huddle';
  id: string;
  environmentId?: string;
  flockId?: string;
  summary: string;
  severity: SignalSeverity;
  timestamp: string;
}

export function normalizeValkyrieSignalEvent(raw: unknown): ValkyrieSignalEvent | null {
  if (!raw || typeof raw !== 'object') return null;
  const payload = raw as Record<string, unknown>;
  const rawType = typeof payload.type === 'string' ? payload.type : 'signal';
  const type: ValkyrieSignalEvent['type'] =
    rawType === 'judgment' || rawType === 'action' || rawType === 'learning' || rawType === 'huddle'
      ? rawType
      : 'signal';
  const rawSeverity = typeof payload.severity === 'string' ? payload.severity : 'info';
  const severity: SignalSeverity =
    rawSeverity === 'notice' || rawSeverity === 'warning' || rawSeverity === 'critical'
      ? rawSeverity
      : 'info';
  const summary =
    typeof payload.summary === 'string'
      ? payload.summary
      : typeof payload.message === 'string'
        ? payload.message
        : '';
  if (!summary) return null;
  const timestamp =
    typeof payload.timestamp === 'string'
      ? payload.timestamp
      : typeof payload.receivedAt === 'string'
        ? payload.receivedAt
        : new Date(0).toISOString();
  return {
    type,
    id: typeof payload.id === 'string' && payload.id ? payload.id : `${timestamp}:${summary}`,
    environmentId: typeof payload.environmentId === 'string' ? payload.environmentId : undefined,
    flockId: typeof payload.flockId === 'string' ? payload.flockId : undefined,
    summary,
    severity,
    timestamp,
  };
}
