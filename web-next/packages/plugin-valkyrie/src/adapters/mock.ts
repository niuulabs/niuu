import type {
  ActionRecord,
  CourtDecision,
  EnvironmentSignal,
  EnvironmentSummary,
  FlockSummary,
  HuddleMessage,
  HuddleSummary,
  JudgmentRecord,
  LearningRecord,
  OperationalState,
  ValkyrieDashboard,
  ValkyrieResident,
  ValkyrieSignalEvent,
} from '../domain';
import type {
  AutonomyUpdateRequest,
  HuddleSendRequest,
  IValkyrieService,
  IValkyrieSignalStream,
  LearningDecisionRequest,
  ValkyrieSignalListener,
} from '../ports';

const UPDATED_AT = '2026-06-03T14:10:00Z';

const environments: EnvironmentSummary[] = [
  {
    id: 'env-k8s-valhalla',
    name: 'Valhalla k8s',
    kind: 'kubernetes',
    health: 'watch',
    flockId: 'flock-k8s',
    topologyNodeIds: ['realm-asgard', 'cluster-valhalla', 'valkyrie-valhalla-sigrun'],
    signalCount: 24,
    unresolvedSignalCount: 3,
    wakefulCount: 2,
    dreamingCount: 1,
    lastSignalAt: '2026-06-03T14:08:00Z',
  },
  {
    id: 'env-host-jozef',
    name: 'Jozef host',
    kind: 'host',
    health: 'healthy',
    flockId: 'flock-personal',
    topologyNodeIds: ['host-macbook', 'valkyrie-host-email'],
    signalCount: 9,
    unresolvedSignalCount: 1,
    wakefulCount: 1,
    dreamingCount: 0,
    lastSignalAt: '2026-06-03T13:55:00Z',
  },
  {
    id: 'env-printer-forge',
    name: 'Printer forge',
    kind: 'printer',
    health: 'degraded',
    flockId: 'flock-printers',
    topologyNodeIds: ['host-printer-pi-1', 'valkyrie-printer-eir'],
    signalCount: 14,
    unresolvedSignalCount: 4,
    wakefulCount: 1,
    dreamingCount: 0,
    lastSignalAt: '2026-06-03T14:03:00Z',
  },
];

const valkyries: ValkyrieResident[] = [
  {
    id: 'valkyrie-valhalla-sigrun',
    name: 'Sigrun',
    environmentId: 'env-k8s-valhalla',
    flockId: 'flock-k8s',
    persona: 'cluster-guardian',
    specialty: 'k8s event triage',
    wakefulness: 'awake',
    autonomyMode: 'delegated',
    status: 'busy',
    confidence: 0.86,
    inboxSubjects: ['k8s.event.*', 'sleipnir.learning.*', 'ravn.action.*'],
    toolCount: 17,
    lastDreamAt: '2026-06-03T03:00:00Z',
    lastActionAt: '2026-06-03T14:06:00Z',
  },
  {
    id: 'valkyrie-valhalla-runa',
    name: 'Runa',
    environmentId: 'env-k8s-valhalla',
    flockId: 'flock-k8s',
    persona: 'state-maintainer',
    specialty: 'desired-state drift',
    wakefulness: 'dreaming',
    autonomyMode: 'supervised',
    status: 'online',
    confidence: 0.78,
    inboxSubjects: ['observatory.topology', 'mimir.learning.*'],
    toolCount: 11,
    lastDreamAt: '2026-06-03T14:00:00Z',
  },
  {
    id: 'valkyrie-host-email',
    name: 'Saga',
    environmentId: 'env-host-jozef',
    flockId: 'flock-personal',
    persona: 'personal-sentinel',
    specialty: 'email importance',
    wakefulness: 'watching',
    autonomyMode: 'manual',
    status: 'online',
    confidence: 0.74,
    inboxSubjects: ['gmail.message.received', 'calendar.event.updated'],
    toolCount: 6,
  },
  {
    id: 'valkyrie-printer-eir',
    name: 'Eir',
    environmentId: 'env-printer-forge',
    flockId: 'flock-printers',
    persona: 'printer-operator',
    specialty: 'resin printer operations',
    wakefulness: 'awake',
    autonomyMode: 'delegated',
    status: 'blocked',
    confidence: 0.69,
    inboxSubjects: ['printer.print.done', 'printer.resin.low', 'pi.sensor.*'],
    toolCount: 8,
    lastActionAt: '2026-06-03T13:42:00Z',
  },
];

const flocks: FlockSummary[] = [
  {
    id: 'flock-k8s',
    name: 'Kubernetes Valkyries',
    domain: 'kubernetes',
    natsSubject: 'flock.k8s.>',
    environmentIds: ['env-k8s-valhalla'],
    valkyrieIds: ['valkyrie-valhalla-sigrun', 'valkyrie-valhalla-runa'],
    learningIds: ['learn-k8s-oom-canary', 'learn-k8s-noisy-probe'],
    health: 'watch',
    lastExchangeAt: '2026-06-03T14:05:00Z',
  },
  {
    id: 'flock-personal',
    name: 'Personal Sentinels',
    domain: 'personal-ops',
    natsSubject: 'flock.personal.>',
    environmentIds: ['env-host-jozef'],
    valkyrieIds: ['valkyrie-host-email'],
    learningIds: ['learn-email-vendor-escalation'],
    health: 'healthy',
    lastExchangeAt: '2026-06-03T13:58:00Z',
  },
  {
    id: 'flock-printers',
    name: 'Printer Operators',
    domain: 'fabrication',
    natsSubject: 'flock.printers.>',
    environmentIds: ['env-printer-forge'],
    valkyrieIds: ['valkyrie-printer-eir'],
    learningIds: ['learn-printer-resin-stall'],
    health: 'degraded',
    lastExchangeAt: '2026-06-03T13:44:00Z',
  },
];

const signals: EnvironmentSignal[] = [
  {
    id: 'sig-k8s-imagepull',
    environmentId: 'env-k8s-valhalla',
    source: 'k8s/events',
    subject: 'pod/ravn-worker-77',
    summary: 'ImagePullBackOff after registry token refresh',
    severity: 'warning',
    status: 'acting',
    receivedAt: '2026-06-03T14:08:00Z',
    assignedValkyrieId: 'valkyrie-valhalla-sigrun',
    labels: ['k8s', 'registry', 'workload'],
  },
  {
    id: 'sig-k8s-oom',
    environmentId: 'env-k8s-valhalla',
    source: 'k8s/events',
    subject: 'deployment/sleipnir-api',
    summary: 'OOMKilled pattern matches learned memory pressure case',
    severity: 'critical',
    status: 'triaged',
    receivedAt: '2026-06-03T14:02:00Z',
    assignedValkyrieId: 'valkyrie-valhalla-runa',
    labels: ['k8s', 'memory', 'canary-learning'],
  },
  {
    id: 'sig-email-contract',
    environmentId: 'env-host-jozef',
    source: 'gmail',
    subject: 'Important contract review',
    summary: 'External sender asks for review before Friday',
    severity: 'notice',
    status: 'new',
    receivedAt: '2026-06-03T13:55:00Z',
    assignedValkyrieId: 'valkyrie-host-email',
    labels: ['email', 'human-review'],
  },
  {
    id: 'sig-printer-resin',
    environmentId: 'env-printer-forge',
    source: 'printer/pi-1',
    subject: 'Saturn 4 Ultra',
    summary: 'Resin low and print paused at layer 812',
    severity: 'critical',
    status: 'acting',
    receivedAt: '2026-06-03T14:03:00Z',
    assignedValkyrieId: 'valkyrie-printer-eir',
    labels: ['printer', 'resin', 'blocked'],
  },
];

const operationalStates: OperationalState[] = [
  {
    id: 'state-k8s-capacity',
    environmentId: 'env-k8s-valhalla',
    name: 'Cluster capacity',
    desired: 'No critical workloads pending',
    observed: '1 pending workload, 2 nodes memory constrained',
    drift: 'major',
    maintainedBy: ['valkyrie-valhalla-runa'],
    updatedAt: '2026-06-03T14:08:00Z',
  },
  {
    id: 'state-email-focus',
    environmentId: 'env-host-jozef',
    name: 'Inbox focus',
    desired: 'Only important messages reach operator',
    observed: '1 message queued for review',
    drift: 'minor',
    maintainedBy: ['valkyrie-host-email'],
    updatedAt: '2026-06-03T13:55:00Z',
  },
  {
    id: 'state-printer-continuity',
    environmentId: 'env-printer-forge',
    name: 'Print continuity',
    desired: 'Printer can finish active print',
    observed: 'Paused for low resin',
    drift: 'major',
    maintainedBy: ['valkyrie-printer-eir'],
    updatedAt: '2026-06-03T14:03:00Z',
  },
];

const judgments: JudgmentRecord[] = [
  {
    id: 'judge-k8s-imagepull',
    environmentId: 'env-k8s-valhalla',
    signalId: 'sig-k8s-imagepull',
    valkyrieId: 'valkyrie-valhalla-sigrun',
    verdict: 'act',
    confidence: 0.82,
    rationale: 'Known registry token rollover; safe delegated remediation path exists.',
    createdAt: '2026-06-03T14:08:20Z',
  },
  {
    id: 'judge-email-contract',
    environmentId: 'env-host-jozef',
    signalId: 'sig-email-contract',
    valkyrieId: 'valkyrie-host-email',
    verdict: 'escalate',
    confidence: 0.74,
    rationale: 'Message mentions contractual review and deadline.',
    createdAt: '2026-06-03T13:55:20Z',
  },
];

const courtDecisions: CourtDecision[] = [
  {
    id: 'court-k8s-rollout',
    environmentId: 'env-k8s-valhalla',
    title: 'Restart affected ravn worker deployment',
    status: 'approved',
    risk: 'medium',
    decidedBy: ['valkyrie-valhalla-sigrun', 'valkyrie-valhalla-runa'],
    createdAt: '2026-06-03T14:08:30Z',
  },
  {
    id: 'court-email-draft',
    environmentId: 'env-host-jozef',
    title: 'Draft reply for operator review',
    status: 'pending',
    risk: 'hard_gate',
    decidedBy: ['valkyrie-host-email'],
    createdAt: '2026-06-03T13:56:00Z',
  },
];

const actions: ActionRecord[] = [
  {
    id: 'act-k8s-refresh-secret',
    environmentId: 'env-k8s-valhalla',
    title: 'Refresh registry pull secret and restart worker',
    status: 'running',
    risk: 'medium',
    ownerValkyrieId: 'valkyrie-valhalla-sigrun',
    startedAt: '2026-06-03T14:08:40Z',
  },
  {
    id: 'act-printer-notify',
    environmentId: 'env-printer-forge',
    title: 'Pause queue and request resin refill',
    status: 'succeeded',
    risk: 'low',
    ownerValkyrieId: 'valkyrie-printer-eir',
    startedAt: '2026-06-03T14:03:30Z',
    finishedAt: '2026-06-03T14:04:10Z',
  },
];

const huddles: HuddleSummary[] = [
  {
    id: 'huddle-valhalla-now',
    environmentId: 'env-k8s-valhalla',
    title: 'Valhalla memory and registry huddle',
    status: 'open',
    participantIds: ['valkyrie-valhalla-sigrun', 'valkyrie-valhalla-runa'],
    joined: false,
    lastActivityAt: '2026-06-03T14:08:45Z',
    messages: [
      {
        id: 'msg-huddle-1',
        huddleId: 'huddle-valhalla-now',
        authorId: 'valkyrie-valhalla-sigrun',
        authorName: 'Sigrun',
        body: 'Registry token refresh is underway. Watching pull failures for convergence.',
        createdAt: '2026-06-03T14:08:45Z',
      },
    ],
  },
  {
    id: 'huddle-printer-pause',
    environmentId: 'env-printer-forge',
    title: 'Printer resin pause',
    status: 'quiet',
    participantIds: ['valkyrie-printer-eir'],
    joined: false,
    lastActivityAt: '2026-06-03T14:04:10Z',
    messages: [
      {
        id: 'msg-huddle-printer-1',
        huddleId: 'huddle-printer-pause',
        authorId: 'valkyrie-printer-eir',
        authorName: 'Eir',
        body: 'Print is paused and operator notification has been sent.',
        createdAt: '2026-06-03T14:04:10Z',
      },
    ],
  },
];

const learnings: LearningRecord[] = [
  {
    id: 'learn-k8s-oom-canary',
    title: 'OOMKilled with rising queue depth',
    summary:
      'Classify paired OOMKilled and queue-depth rise as capacity drift before restart loops.',
    scope: 'flock',
    status: 'canary',
    sourceEnvironmentId: 'env-k8s-valhalla',
    sourceValkyrieId: 'valkyrie-valhalla-runa',
    targetFlockId: 'flock-k8s',
    confidence: 0.81,
    evaluation: '3/3 replayed incidents predicted before user-visible failures.',
    negativeTransferRisk: 'medium',
    redaction: 'none',
    promotedTool: 'k8s_memory_pressure_probe',
    createdAt: '2026-06-03T03:20:00Z',
  },
  {
    id: 'learn-email-vendor-escalation',
    title: 'Vendor deadline language',
    summary: 'Flag messages with contract, deadline, and external sender as review-worthy.',
    scope: 'private',
    status: 'candidate',
    sourceEnvironmentId: 'env-host-jozef',
    sourceValkyrieId: 'valkyrie-host-email',
    confidence: 0.72,
    evaluation: 'Needs operator feedback after three more inbox examples.',
    negativeTransferRisk: 'low',
    redaction: 'partial',
    createdAt: '2026-06-02T19:10:00Z',
  },
  {
    id: 'learn-printer-resin-stall',
    title: 'Resin low before layer stalls',
    summary:
      'Pause active print queue and notify before resin starvation causes partial cure defects.',
    scope: 'environment',
    status: 'adopted',
    sourceEnvironmentId: 'env-printer-forge',
    sourceValkyrieId: 'valkyrie-printer-eir',
    confidence: 0.89,
    evaluation: 'Prevented two failed prints in local replay.',
    negativeTransferRisk: 'low',
    redaction: 'none',
    promotedTool: 'printer_resin_pause',
    createdAt: '2026-06-01T22:00:00Z',
  },
];

export function createSeedValkyrieDashboard(): ValkyrieDashboard {
  return {
    environments: environments.map((entry) => ({ ...entry })),
    valkyries: valkyries.map((entry) => ({ ...entry, inboxSubjects: [...entry.inboxSubjects] })),
    flocks: flocks.map((entry) => ({
      ...entry,
      environmentIds: [...entry.environmentIds],
      valkyrieIds: [...entry.valkyrieIds],
      learningIds: [...entry.learningIds],
    })),
    signals: signals.map((entry) => ({ ...entry, labels: [...entry.labels] })),
    operationalStates: operationalStates.map((entry) => ({
      ...entry,
      maintainedBy: [...entry.maintainedBy],
    })),
    judgments: judgments.map((entry) => ({ ...entry })),
    courtDecisions: courtDecisions.map((entry) => ({ ...entry, decidedBy: [...entry.decidedBy] })),
    actions: actions.map((entry) => ({ ...entry })),
    huddles: huddles.map((entry) => ({
      ...entry,
      participantIds: [...entry.participantIds],
      messages: entry.messages.map((message) => ({ ...message })),
    })),
    learnings: learnings.map((entry) => ({ ...entry })),
    liveReport: {
      title: 'K8s flock routing',
      status: 'watch',
      lastObservedAt: UPDATED_AT,
      totalMessages: 71808,
      sharedStream: 'flock-k8s-events',
      routeSubject: 'flock.k8s.>',
      projectionMode: 'mixed',
      transports: [
        {
          id: 'transport-valhalla',
          label: 'Valhalla k8s',
          environmentId: 'env-k8s-valhalla',
          account: 'obs-valhalla',
          streamName: 'obs-valhalla-events',
          subjectPrefix: 'obs.valhalla',
          messageCount: 39062,
          signalCount: 3280,
          activityCount: 35679,
          judgmentCount: 74,
          actionCount: 29,
          rejectedCount: 18,
          consumerFilterSubjects: [
            'obs.valhalla.ravn.mesh.rpc.valkyrie_valhalla_k8s',
            'obs.valhalla.ravn.mesh.signal.kubernetes.event',
            'obs.valhalla.ravn.mesh.valkyrie.judgment.>',
            'flock.k8s.>',
          ],
          health: 'watch',
          lastMessageAt: UPDATED_AT,
          notes: ['Local signals stay local.', 'Flock outcomes project into flock.k8s.>.'],
        },
        {
          id: 'transport-ymir',
          label: 'Ymir k8s',
          environmentId: 'env-k8s-ymir',
          account: 'obs-ymir',
          streamName: 'obs-ymir-events',
          subjectPrefix: 'obs.ymir',
          messageCount: 32746,
          signalCount: 5133,
          activityCount: 27522,
          judgmentCount: 67,
          actionCount: 24,
          rejectedCount: 15,
          consumerFilterSubjects: [
            'obs.ymir.ravn.mesh.rpc.valkyrie_ymir_k8s',
            'obs.ymir.ravn.mesh.signal.kubernetes.event',
            'obs.ymir.ravn.mesh.valkyrie.judgment.>',
            'flock.k8s.>',
          ],
          health: 'watch',
          lastMessageAt: UPDATED_AT,
          notes: ['Ymir remains the hub.', 'Shared k8s learning uses the flock projection.'],
        },
      ],
      findings: [
        'Existing NATS and Sleipnir paths are the bus.',
        'Durables are split per filter subject.',
        'The UI tracks local health and flock-sharing health.',
      ],
    },
    updatedAt: UPDATED_AT,
  };
}

function replaceLearning(
  dashboard: ValkyrieDashboard,
  learningId: string,
  status: LearningRecord['status'],
): LearningRecord {
  const learning = dashboard.learnings.find((entry) => entry.id === learningId);
  if (!learning) throw new Error(`Learning not found: ${learningId}`);
  const next = { ...learning, status };
  dashboard.learnings = dashboard.learnings.map((entry) =>
    entry.id === learningId ? next : entry,
  );
  return next;
}

export function createMockValkyrieService(seed = createSeedValkyrieDashboard()): IValkyrieService {
  const dashboard: ValkyrieDashboard = seed;

  return {
    async getDashboard() {
      return dashboard;
    },
    async listEnvironments() {
      return dashboard.environments;
    },
    async getEnvironment(environmentId) {
      return dashboard.environments.find((entry) => entry.id === environmentId) ?? null;
    },
    async listFlocks() {
      return dashboard.flocks;
    },
    async getFlock(flockId) {
      return dashboard.flocks.find((entry) => entry.id === flockId) ?? null;
    },
    async joinHuddle(huddleId) {
      const huddle = dashboard.huddles.find((entry) => entry.id === huddleId);
      if (!huddle) throw new Error(`Huddle not found: ${huddleId}`);
      huddle.joined = true;
      return huddle;
    },
    async sendHuddleMessage(request: HuddleSendRequest) {
      const huddle = dashboard.huddles.find((entry) => entry.id === request.huddleId);
      if (!huddle) throw new Error(`Huddle not found: ${request.huddleId}`);
      const message: HuddleMessage = {
        id: `msg-${request.huddleId}-${huddle.messages.length + 1}`,
        huddleId: request.huddleId,
        authorId: request.authorId ?? 'operator',
        authorName: request.authorId ?? 'Operator',
        body: request.body,
        createdAt: new Date().toISOString(),
        directedTo: request.directedTo,
      };
      huddle.messages = [...huddle.messages, message];
      huddle.lastActivityAt = message.createdAt;
      return message;
    },
    async leaveHuddle(huddleId) {
      const huddle = dashboard.huddles.find((entry) => entry.id === huddleId);
      if (!huddle) throw new Error(`Huddle not found: ${huddleId}`);
      huddle.joined = false;
      return huddle;
    },
    async adoptLearning(request: LearningDecisionRequest) {
      void request.reason;
      void request.operatorId;
      return replaceLearning(dashboard, request.learningId, 'adopted');
    },
    async rejectLearning(request: LearningDecisionRequest) {
      void request.reason;
      void request.operatorId;
      return replaceLearning(dashboard, request.learningId, 'rejected');
    },
    async overrideLearning(request: LearningDecisionRequest) {
      void request.reason;
      void request.operatorId;
      return replaceLearning(dashboard, request.learningId, 'canary');
    },
    async updateAutonomy(request: AutonomyUpdateRequest) {
      void request.reason;
      dashboard.valkyries = dashboard.valkyries.map((entry) =>
        entry.id === request.valkyrieId ? { ...entry, autonomyMode: request.mode } : entry,
      );
      return dashboard;
    },
  };
}

export function createMockValkyrieSignalStream(
  events: ValkyrieSignalEvent[] = signals.map((signal) => ({
    type: 'signal',
    id: signal.id,
    environmentId: signal.environmentId,
    summary: signal.summary,
    severity: signal.severity,
    timestamp: signal.receivedAt,
  })),
): IValkyrieSignalStream {
  const listeners = new Set<ValkyrieSignalListener>();

  return {
    subscribe(listener) {
      listeners.add(listener);
      for (const event of events) listener(event);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
