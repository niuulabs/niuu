export type EnvironmentKind = 'kubernetes' | 'host' | 'printer' | 'generic';
export type EnvironmentHealth = 'healthy' | 'watch' | 'degraded' | 'critical';
export type WakefulnessState = 'awake' | 'watching' | 'dreaming' | 'resting';
export type AutonomyMode = 'manual' | 'supervised' | 'delegated' | 'yolo';
export type SignalSeverity = 'info' | 'notice' | 'warning' | 'critical';
export type SignalStatus = 'new' | 'triaged' | 'acting' | 'resolved' | 'ignored';
export type ActionStatus = 'planned' | 'running' | 'succeeded' | 'failed' | 'rolled_back';
export type LearningScope = 'private' | 'environment' | 'domain' | 'flock' | 'shared';
export type LearningStatus = 'candidate' | 'canary' | 'adopted' | 'rejected' | 'rolled_back';

export interface EnvironmentSummary {
  id: string;
  name: string;
  kind: EnvironmentKind;
  health: EnvironmentHealth;
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
  title: string;
  status: 'open' | 'quiet' | 'closed';
  participantIds: string[];
  joined: boolean;
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
