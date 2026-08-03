export type EnvironmentKind = 'kubernetes' | 'host' | 'printer' | 'generic';
export type EnvironmentHealth = 'healthy' | 'watch' | 'degraded' | 'critical';
export type WakefulnessState = 'sleeping' | 'watching' | 'wakeful' | 'dreaming';
export type AutonomyMode = 'guarded' | 'autonomous' | 'yolo';
export type SignalSeverity = 'info' | 'notice' | 'warning' | 'critical';
export type SignalStatus = 'new' | 'triaged' | 'acting' | 'resolved' | 'ignored';
export type ActionStatus = 'planned' | 'running' | 'succeeded' | 'failed' | 'rolled_back';
export type LearningScope = 'private' | 'environment' | 'domain' | 'flock' | 'shared';
export type LearningStatus =
  'requested' | 'candidate' | 'canary' | 'adopted' | 'rejected' | 'rolled_back' | 'completed';

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
  /** The human seed: what this resident stewards and what "better" means. */
  charter?: string;
  /** Signal severities that trigger an autonomous investigation task. */
  signalTaskSeverities?: string[];
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

// ---------------------------------------------------------------------------
// Durable decision history — served by /decisions, /signals/history
// ---------------------------------------------------------------------------

export interface DecisionRecord {
  decisionId: string;
  environmentId: string;
  valkyrieId: string;
  operationalState: string;
  tier: string;
  wakefulness?: string;
  confidence: number;
  rationale: string;
  recommendedAction: string;
  actionAuthority: string;
  actionCapability?: string;
  signalRefs: string[];
  evidence: Array<Record<string, unknown>>;
  correlationId: string;
  summary: string;
  source?: string;
  outcome: string;
  outcomeDetail?: string;
  outcomeAt?: string;
  reviewItemId?: string;
  decidedAt: string;
}

export interface SignalHistoryEntry {
  signalId: string;
  environmentId: string;
  eventType: string;
  source: string;
  subject: string;
  summary: string;
  severity: string;
  correlationId?: string;
  receivedAt: string;
}

export interface ActionHistoryEntry {
  actionId: string;
  eventId: string;
  eventType: string;
  status: string;
  environmentId: string;
  valkyrieId: string;
  capability: string;
  actionAuthority: string;
  outcome: string;
  rationale: string;
  dryRun: boolean;
  correlationId: string;
  summary: string;
  observedAt: string;
}

export interface HistoryPage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface DecisionDetail {
  decision: DecisionRecord;
  lineage: {
    signals: SignalHistoryEntry[];
    actions: ActionHistoryEntry[];
    review: Record<string, unknown> | null;
  };
}

export interface SkillUsageStat {
  skillName: string;
  capability: string;
  environmentId: string;
  uses: number;
  successes: number;
  failures: number;
  lastUsedAt: string;
  lastOutcome: string;
  rolledBackAt: string;
}

// ---------------------------------------------------------------------------
// Learned skills — the installed artifacts behind "handled with a learned
// skill" judgments, served by /skills (see IValkyrieSkillsService).
// ---------------------------------------------------------------------------

export interface LearnedSkillSummary {
  skillName: string;
  environmentId: string;
  valkyrieId: string;
  description: string;
  learningId: string;
  adoptedAt: string;
  observedAt?: string;
  hasCode: boolean;
}

export interface LearnedSkillRecord extends LearnedSkillSummary {
  content: string;
  toolCode: string;
  testCode: string;
  requirements: string[];
  manifest: Record<string, unknown>;
}

/** Skill name pinned in a decision's evidence, empty when absent. */
export function decisionSkillName(decision: Pick<DecisionRecord, 'evidence'>): string {
  for (const entry of decision.evidence) {
    const name = entry['skill_name'];
    if (typeof name === 'string' && name) return name;
  }
  return '';
}

/**
 * The learned skill a decision references: the explicit evidence `skill_name`
 * wins; otherwise the first known skill name mentioned in the summary or
 * rationale text ("handled a signal with learned skill 'X'").
 */
export function referencedSkillName(
  decision: Pick<DecisionRecord, 'evidence' | 'summary' | 'rationale'>,
  knownSkillNames: readonly string[],
): string {
  const explicit = decisionSkillName(decision);
  if (explicit) return explicit;
  const haystack = `${decision.summary} ${decision.rationale}`;
  return knownSkillNames.find((name) => name && haystack.includes(name)) ?? '';
}

/**
 * Keep each visible list page small enough to scan. Panels either slice to this
 * cap or use it as their page size; filters and pagination can still reach
 * older items.
 */
export const LIST_LIMIT = 20;

/** Activity stories are heavier than rows; cap the tail a bit higher. */
export const ACTIVITY_STORY_LIMIT = 30;

/**
 * Tiers that reach the operator. `ambient`/`observational` judgments are
 * background noise the resident records but never asks about.
 */
const OPERATOR_TIERS = new Set(['present', 'urgent']);

/**
 * Recommended-action values that are NOT a real action: an empty/observational
 * verdict never needs approval no matter the authority or tier.
 */
const NON_ACTIONS = new Set(['', 'none', 'n/a', 'na', 'watch', 'observe', 'noop']);

/** True when the decision's recommendedAction names a real, executable action. */
export function decisionHasRealAction(
  decision: Pick<DecisionRecord, 'recommendedAction'>,
): boolean {
  return !NON_ACTIONS.has((decision.recommendedAction ?? '').trim().toLowerCase());
}

/**
 * Mirror of the backend inbox gate: a judgment only truly awaits the operator
 * when it would land in the review inbox. That needs all three:
 *   - actionAuthority === 'human_review_required'
 *   - tier is present or urgent (ambient/observational never surfaces)
 *   - recommendedAction is a real action (not '', none, watch, observe…)
 *
 * Everything else is observational or autonomous and must NOT read as
 * "needs your approval".
 */
export function decisionNeedsApproval(
  decision: Pick<DecisionRecord, 'actionAuthority' | 'tier' | 'recommendedAction'>,
): boolean {
  if (decision.actionAuthority !== 'human_review_required') return false;
  if (!OPERATOR_TIERS.has((decision.tier ?? '').trim().toLowerCase())) return false;
  return decisionHasRealAction(decision);
}

const REDUNDANT_PREFIX = /^valkyrie(?:\s+|:\s*)\S+\s+in\s+\S+\s+/i;

/**
 * The row headline: the record's own `summary` sentence, stripped of any
 * redundant leading "Valkyrie <id> in <env> " prefix (the page already shows
 * whose decision it is). Empty summary falls back to the caller-supplied
 * label (the copy.ts operational-state label).
 */
export function decisionHeadline(
  decision: Pick<DecisionRecord, 'summary'>,
  fallbackLabel: string,
): string {
  const summary = decision.summary?.trim() ?? '';
  if (!summary) return fallbackLabel;
  return summary.replace(REDUNDANT_PREFIX, '').trim() || fallbackLabel;
}

/**
 * A short subject for the decision row: the resource name from evidence
 * (subject/target/resource/pod/deployment) when present, else a readable
 * form of the correlationId (dropping an `idle-triage:`/`corr-` prefix).
 */
export function decisionSubject(
  decision: Pick<DecisionRecord, 'evidence' | 'correlationId'>,
): string {
  const keys = ['subject', 'resource', 'target', 'pod', 'deployment', 'object', 'name'];
  for (const entry of decision.evidence) {
    for (const key of keys) {
      const value = entry[key];
      if (typeof value === 'string' && value) return value;
    }
  }
  const correlation = decision.correlationId ?? '';
  if (!correlation) return '';
  const withoutIdle = correlation.replace(/^idle-triage:/, '');
  return withoutIdle.replace(/^corr-/, '');
}

export interface GroupedDecision {
  /** The newest decision in the group — what the row renders. */
  decision: DecisionRecord;
  /** How many near-identical decisions collapsed into this row (>=1). */
  count: number;
}

/**
 * Collapse consecutive decisions sharing a correlationId into one row — the
 * same subject re-judged every few minutes should read as a single situation
 * with a "×N" badge, not N near-duplicate rows. Input is assumed newest-first
 * (as the API returns it); the newest decision in each run wins and its
 * timestamp is the one shown. A correlationId that recurs after an unrelated
 * decision starts a fresh group, so genuinely distinct situations stay split.
 */
export function collapseDecisionsByCorrelation(
  decisions: readonly DecisionRecord[],
): GroupedDecision[] {
  const groups: GroupedDecision[] = [];
  for (const decision of decisions) {
    const last = groups[groups.length - 1];
    const correlation = decision.correlationId || '';
    if (last && correlation && last.decision.correlationId === correlation) {
      last.count += 1;
      // Newest-first input means the first of a run is already the latest;
      // keep it and only bump the count for the rest.
      continue;
    }
    groups.push({ decision, count: 1 });
  }
  return groups;
}

// ---------------------------------------------------------------------------
// Roster sidebar — grouping, filtering, and per-resident activity
// ---------------------------------------------------------------------------

export type RosterGroupMode = 'kind' | 'environment' | 'flock';

export const ROSTER_GROUP_MODES: readonly RosterGroupMode[] = ['kind', 'environment', 'flock'];

export const ROSTER_GROUP_MODE_LABELS: Record<RosterGroupMode, string> = {
  kind: 'by env type',
  environment: 'by environment',
  flock: 'by flock',
};

const ENVIRONMENT_KIND_LABELS: Record<EnvironmentKind, string> = {
  kubernetes: 'Kubernetes',
  host: 'Inbox / Host',
  printer: 'Printer / Pi Cell',
  generic: 'Other',
};

export function environmentKindLabel(kind: EnvironmentKind): string {
  return ENVIRONMENT_KIND_LABELS[kind];
}

/** A roster row: the resident joined to its environment and flock. */
export interface RosterEntry {
  valkyrie: ValkyrieResident;
  environment?: EnvironmentSummary;
  flock?: FlockSummary;
}

export function rosterEntries(
  dashboard: Pick<ValkyrieDashboard, 'valkyries' | 'environments' | 'flocks'>,
): RosterEntry[] {
  return dashboard.valkyries.map((valkyrie) => {
    const environment = dashboard.environments.find((entry) => entry.id === valkyrie.environmentId);
    const flockId = valkyrie.flockId ?? environment?.flockId;
    const flock = dashboard.flocks.find(
      (entry) => entry.id === flockId || entry.valkyrieIds.includes(valkyrie.id),
    );
    return { valkyrie, environment, flock };
  });
}

/** Case-insensitive match on resident name, specialty, environment, or flock. */
export function filterRosterEntries(entries: readonly RosterEntry[], query: string): RosterEntry[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...entries];
  return entries.filter(({ valkyrie, environment, flock }) =>
    [
      valkyrie.name,
      valkyrie.specialty,
      valkyrie.environmentId,
      environment?.name,
      flock?.name,
    ].some((value) => value?.toLowerCase().includes(needle)),
  );
}

export interface RosterGroup {
  key: string;
  label: string;
  /** Environment kind driving the group icon, when the group maps to one. */
  kind?: EnvironmentKind;
  entries: RosterEntry[];
}

const KIND_ORDER: readonly EnvironmentKind[] = ['kubernetes', 'host', 'printer', 'generic'];

/**
 * Group roster entries for the sidebar. `kind` groups by environment type in
 * a fixed order (the default view); `environment` and `flock` keep dashboard
 * order. Residents whose flock is unknown land in a "No flock" group rather
 * than disappearing.
 */
export function groupRosterEntries(
  entries: readonly RosterEntry[],
  mode: RosterGroupMode,
): RosterGroup[] {
  const groups = new Map<string, RosterGroup>();
  const push = (key: string, label: string, entry: RosterEntry, kind?: EnvironmentKind) => {
    const existing = groups.get(key);
    if (existing) {
      existing.entries.push(entry);
      return;
    }
    groups.set(key, { key, label, kind, entries: [entry] });
  };
  for (const entry of entries) {
    if (mode === 'flock') {
      push(entry.flock?.id ?? 'no-flock', entry.flock?.name ?? 'No flock', entry);
      continue;
    }
    if (mode === 'environment') {
      const environmentId = entry.environment?.id ?? entry.valkyrie.environmentId;
      const label = entry.environment?.name ?? entry.valkyrie.environmentId;
      push(environmentId, label, entry, entry.environment?.kind);
      continue;
    }
    const kind = entry.environment?.kind ?? 'generic';
    push(kind, environmentKindLabel(kind), entry, kind);
  }
  const ordered = [...groups.values()];
  if (mode === 'kind') {
    ordered.sort(
      (a, b) => KIND_ORDER.indexOf(a.kind ?? 'generic') - KIND_ORDER.indexOf(b.kind ?? 'generic'),
    );
  }
  return ordered;
}

/** The roster activity strip: this many windows of this many minutes each. */
export const ACTIVITY_BAR_COUNT = 4;
export const ACTIVITY_BAR_MINUTES = 15;

/**
 * The huddle an operator can join for an environment: the most recently
 * active huddle that is not closed, preferring open over quiet ones.
 */
export function openHuddleForEnvironment(
  huddles: readonly HuddleSummary[],
  environmentId: string,
): HuddleSummary | undefined {
  const candidates = huddles
    .filter((huddle) => huddle.environmentId === environmentId && huddle.status !== 'closed')
    .sort((a, b) => {
      if (a.status !== b.status) return a.status === 'open' ? -1 : 1;
      return b.lastActivityAt.localeCompare(a.lastActivityAt);
    });
  return candidates[0];
}

/** The resident's freshest timestamp: observed, acted, or dreamt. */
export function valkyrieLastSeenAt(
  valkyrie: Pick<ValkyrieResident, 'lastObservedAt' | 'lastActionAt' | 'lastDreamAt'>,
): string | undefined {
  return [valkyrie.lastObservedAt, valkyrie.lastActionAt, valkyrie.lastDreamAt]
    .filter((value): value is string => Boolean(value))
    .sort((a, b) => b.localeCompare(a))[0];
}

/**
 * The reference instant for the activity strip — the dashboard's own freshest
 * timestamp (snapshot time, telemetry watermark, or newest event). Using data
 * time instead of wall-clock time keeps rendering pure and the bars stable
 * between dashboard refreshes.
 */
export function rosterReferenceTime(dashboard: {
  updatedAt: string;
  telemetry?: {
    lastObservedAt?: string;
    recentEvents?: readonly Pick<ValkyrieEventTelemetry, 'observedAt'>[];
  };
}): number {
  const candidates = [
    dashboard.updatedAt,
    dashboard.telemetry?.lastObservedAt,
    ...(dashboard.telemetry?.recentEvents ?? []).map((event) => event.observedAt),
  ];
  return candidates.reduce((max, value) => {
    const parsed = value ? Date.parse(value) : Number.NaN;
    return Number.isNaN(parsed) ? max : Math.max(max, parsed);
  }, 0);
}

/**
 * Telemetry events credited to a resident, bucketed into ACTIVITY_BAR_COUNT
 * windows of ACTIVITY_BAR_MINUTES each, newest window first. An event counts
 * when it names the valkyrie, or names no valkyrie but happened in its
 * environment — an event attributed to a sibling resident never counts.
 */
export function rosterActivityBars(
  events: readonly Pick<ValkyrieEventTelemetry, 'environmentId' | 'valkyrieId' | 'observedAt'>[],
  valkyrie: Pick<ValkyrieResident, 'id' | 'environmentId'>,
  now: number,
): number[] {
  const buckets = new Array<number>(ACTIVITY_BAR_COUNT).fill(0);
  const bucketMs = ACTIVITY_BAR_MINUTES * 60_000;
  for (const event of events) {
    const mine = event.valkyrieId
      ? event.valkyrieId === valkyrie.id
      : event.environmentId === valkyrie.environmentId;
    if (!mine) continue;
    const age = now - Date.parse(event.observedAt);
    if (Number.isNaN(age) || age < 0) continue;
    const bucket = Math.floor(age / bucketMs);
    if (bucket >= ACTIVITY_BAR_COUNT) continue;
    buckets[bucket] = (buckets[bucket] ?? 0) + 1;
  }
  return buckets;
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

/** Operator feedback recorded on a learning — additive, null until given. */
export interface LearningFeedback {
  verdict: string;
  reason: string;
  operatorId: string;
  recordedAt: string;
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
  /** Operator feedback on this learning, null/absent while awaiting. */
  feedback?: LearningFeedback | null;
  /** How often this pattern was independently re-learned (default 1). */
  repetition?: number;
  /** Id of the learning this candidate supersedes, set on revisions. */
  supersedes?: string;
}

// ---------------------------------------------------------------------------
// Learning feedback — the five operator verdicts and the scope ladder the
// wrong_tier verdict may move a learning along.
// ---------------------------------------------------------------------------

export type LearningFeedbackVerdict =
  'useful' | 'good_action' | 'bad_action' | 'dismissed' | 'wrong_tier';

export const LEARNING_FEEDBACK_VERDICTS: ReadonlyArray<{
  verdict: LearningFeedbackVerdict;
  label: string;
}> = [
  { verdict: 'useful', label: 'Useful' },
  { verdict: 'dismissed', label: 'Dismissed' },
  { verdict: 'wrong_tier', label: 'Wrong tier' },
  { verdict: 'bad_action', label: 'Bad action' },
  { verdict: 'good_action', label: 'Good action' },
];

/** Human label for a feedback verdict ("wrong_tier" → "Wrong tier"). */
export function learningFeedbackVerdictLabel(verdict?: string): string {
  if (!verdict) return 'Awaiting';
  const known = LEARNING_FEEDBACK_VERDICTS.find((entry) => entry.verdict === verdict);
  if (known) return known.label;
  return verdict.replace(/_/g, ' ');
}

/** Promotion ladder for learnings, narrowest to widest blast radius. */
export const LEARNING_SCOPE_ORDER: readonly LearningScope[] = [
  'private',
  'environment',
  'flock',
  'domain',
  'shared',
];

/**
 * The scopes a wrong_tier verdict may move a learning to: only the direct
 * promote/demote neighbours on the ordered ladder — a learning never jumps
 * tiers on operator feedback alone.
 */
export function adjacentLearningScopes(scope: LearningScope): LearningScope[] {
  const index = LEARNING_SCOPE_ORDER.indexOf(scope);
  if (index < 0) return [];
  return LEARNING_SCOPE_ORDER.filter((_, position) => Math.abs(position - index) === 1);
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
  /** Event id of the event that directly caused this one, '' when unknown. */
  causationId?: string;
  /** Judgment attention tier (ambient/observational/present/urgent), '' when absent. */
  tier?: string;
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
  charter?: string;
  signalTaskSeverities?: string[];
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

// ---------------------------------------------------------------------------
// ODIN review queue — the one envelope every human decision rides
// ---------------------------------------------------------------------------

export type ReviewKind =
  | 'evolution_build'
  | 'skill_promotion'
  | 'flock_learning'
  | 'court_escalation'
  | 'autonomy_change'
  | 'morning_brief';

export type ReviewStatus =
  'pending' | 'approved' | 'rejected' | 'expired' | 'applied' | 'apply_failed';

export type ReviewRiskClass = 'low' | 'medium' | 'high' | 'critical';

export interface ReviewItem {
  itemId: string;
  kind: ReviewKind;
  requestedAction: string;
  environmentId: string;
  valkyrieId: string;
  title: string;
  summary: string;
  audience: string;
  flockId: string;
  domain: string;
  riskClass: ReviewRiskClass;
  safetyClass: string;
  urgency: number;
  requestedCapability: string;
  evidence: Record<string, unknown>;
  status: ReviewStatus;
  requestedBy: string;
  requestedAt: string;
  decidedBy: string;
  decidedAt: string;
  decisionReason: string;
  resolvedAt: string;
  applyOutcome: string;
  applyDetail: string;
}

export interface ReviewSummary {
  pendingTotal: number;
  pendingByKind: Record<string, number>;
  pendingByRisk: Record<string, number>;
  pendingByEnvironment: Record<string, number>;
  countsByStatus: Record<string, number>;
}

const REVIEW_KINDS: readonly ReviewKind[] = [
  'evolution_build',
  'skill_promotion',
  'flock_learning',
  'court_escalation',
  'autonomy_change',
  'morning_brief',
];

const REVIEW_STATUSES: readonly ReviewStatus[] = [
  'pending',
  'approved',
  'rejected',
  'expired',
  'applied',
  'apply_failed',
];

const REVIEW_RISKS: readonly ReviewRiskClass[] = ['low', 'medium', 'high', 'critical'];

function str(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  return typeof value === 'string' ? value : '';
}

export function normalizeReviewItem(payload: Record<string, unknown>): ReviewItem {
  const kindRaw = str(payload, 'kind');
  const statusRaw = str(payload, 'status');
  const riskRaw = str(payload, 'risk_class');
  const evidence = payload.evidence;
  return {
    itemId: str(payload, 'item_id'),
    kind: (REVIEW_KINDS as readonly string[]).includes(kindRaw)
      ? (kindRaw as ReviewKind)
      : 'flock_learning',
    requestedAction: str(payload, 'requested_action'),
    environmentId: str(payload, 'environment_id'),
    valkyrieId: str(payload, 'valkyrie_id'),
    title: str(payload, 'title') || str(payload, 'item_id'),
    summary: str(payload, 'summary'),
    audience: str(payload, 'audience') || 'valkyrie',
    flockId: str(payload, 'flock_id'),
    domain: str(payload, 'domain'),
    riskClass: (REVIEW_RISKS as readonly string[]).includes(riskRaw)
      ? (riskRaw as ReviewRiskClass)
      : 'low',
    safetyClass: str(payload, 'safety_class') || 'read_only',
    urgency: typeof payload.urgency === 'number' ? payload.urgency : 0.5,
    requestedCapability: str(payload, 'requested_capability') || 'approve',
    evidence:
      typeof evidence === 'object' && evidence !== null
        ? (evidence as Record<string, unknown>)
        : {},
    status: (REVIEW_STATUSES as readonly string[]).includes(statusRaw)
      ? (statusRaw as ReviewStatus)
      : 'pending',
    requestedBy: str(payload, 'requested_by'),
    requestedAt: str(payload, 'requested_at'),
    decidedBy: str(payload, 'decided_by'),
    decidedAt: str(payload, 'decided_at'),
    decisionReason: str(payload, 'decision_reason'),
    resolvedAt: str(payload, 'resolved_at'),
    applyOutcome: str(payload, 'apply_outcome'),
    applyDetail: str(payload, 'apply_detail'),
  };
}

export interface ReviewArtifactEvidence {
  skillContent: string;
  toolCode: string;
  canarySample: Record<string, unknown>;
}

export function reviewArtifactEvidence(item: ReviewItem): ReviewArtifactEvidence {
  const artifact = item.evidence.artifact;
  const record =
    typeof artifact === 'object' && artifact !== null ? (artifact as Record<string, unknown>) : {};
  const canary = record.canary_sample;
  return {
    skillContent: typeof record.content === 'string' ? record.content : '',
    toolCode: typeof record.tool_code === 'string' ? record.tool_code : '',
    canarySample:
      typeof canary === 'object' && canary !== null ? (canary as Record<string, unknown>) : {},
  };
}

export function reviewPolicyFindings(item: ReviewItem): string[] {
  const review = item.evidence.review;
  if (typeof review !== 'object' || review === null) return [];
  const findings = (review as Record<string, unknown>).findings;
  if (!Array.isArray(findings)) return [];
  return findings.filter((entry): entry is string => typeof entry === 'string');
}

/** The investigation prompt/ticket the resident was working when it built the tool. */
export function reviewInvestigationPrompt(item: ReviewItem): string {
  const prompt = item.evidence.investigation_prompt;
  return typeof prompt === 'string' ? prompt : '';
}

// ---------------------------------------------------------------------------
// Realm governance — the trust grant that gates what a realm's Valkyrie
// may build. Types mirror the backend REST casing exactly (snake_case),
// served by /api/v1/realms and /api/v1/ting/workflows.
// ---------------------------------------------------------------------------

/** Realm as returned by GET /api/v1/realms. */
export interface RealmSummary {
  id: string;
  slug: string;
  name: string;
  sleipnir_domain: string | null;
  owner_id: string | null;
  instance_id: string | null;
  autonomy_profile: string;
  created_at: string;
  updated_at: string;
}

/** Trust grant as returned by GET /api/v1/realms/{slug}/trust-grants. */
export interface RealmTrustGrant {
  id: string;
  realm_id: string;
  action_class: string;
  target: string;
  level: number;
  limits: Record<string, unknown>;
  granted_by: string | null;
  granted_at: string;
}

/** The subset of GET /api/v1/ting/workflows the picker reads. */
export interface TingWorkflowSummary {
  id: string;
  name: string;
  description: string;
  version: string;
  tags: string[];
}

export const BUILD_ACTION_CLASS = 'build';
export const TOOL_BUILDER_TAG = 'tool-builder';
export const TRUST_LEVELS = [0, 1, 2, 3, 4, 5] as const;

const ENVIRONMENT_ID_PREFIXES = ['env-k8s-', 'env-'] as const;

/**
 * A realm's slug IS the environment's raw id: `env-k8s-valhalla` is realm
 * `valhalla`, `env-host-jozef` is realm `host-jozef`. Strips the canonical
 * `env-k8s-` (or `env-`) prefix; ids without one are already the slug.
 */
export function realmSlugForEnvironment(environmentId: string): string {
  for (const prefix of ENVIRONMENT_ID_PREFIXES) {
    if (environmentId.startsWith(prefix)) return environmentId.slice(prefix.length);
  }
  return environmentId;
}

/** Trust level → autonomy mode: <=1 guarded, 2–3 autonomous, >=4 yolo. */
export function autonomyModeForLevel(level: number): AutonomyMode {
  if (level >= 4) return 'yolo';
  if (level >= 2) return 'autonomous';
  return 'guarded';
}

/** The realm's effective build grant: the most recently granted `build` entry. */
export function latestBuildGrant(grants: RealmTrustGrant[]): RealmTrustGrant | null {
  const builds = grants
    .filter((grant) => grant.action_class === BUILD_ACTION_CLASS)
    .sort((a, b) => b.granted_at.localeCompare(a.granted_at));
  return builds[0] ?? null;
}

/** Workflow name pinned in the grant's limits, empty when unset. */
export function grantWorkflowName(grant: RealmTrustGrant | null): string {
  const workflow = grant?.limits['workflow'];
  return typeof workflow === 'string' ? workflow : '';
}

export function isToolBuilderWorkflow(workflow: TingWorkflowSummary): boolean {
  return workflow.tags.includes(TOOL_BUILDER_TAG);
}

export function reviewEffectStatement(item: ReviewItem): string {
  switch (item.kind) {
    case 'evolution_build':
      return (
        `Approving will canary the tool in a sandbox, install the skill and tool on ` +
        `${item.environmentId}${item.flockId ? `, and propose it to ${item.flockId}` : ''}.`
      );
    case 'skill_promotion':
      return `Approving will promote ${item.title} to environment scope and announce it to peers.`;
    case 'flock_learning':
      return (
        `Approving will canary and ${item.requestedAction === 'retract' ? 'retract' : 'install'} ` +
        `this learning on every relevant resident in ${item.flockId || 'the flock'}.`
      );
    case 'court_escalation':
      if (typeof item.evidence.operator_question === 'object') {
        return 'Your answer will resume the exact resident case that asked this question.';
      }
      return 'Approving will request execution of the drafted action with operator authority.';
    case 'autonomy_change':
      return `Approving will set ${item.valkyrieId} autonomy as requested.`;
    case 'morning_brief':
      return 'Approving marks this brief as read; no action is executed.';
    default:
      return 'Approving will apply the requested action on the target resident.';
  }
}
