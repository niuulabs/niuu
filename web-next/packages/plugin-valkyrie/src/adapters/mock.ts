import {
  LEARNING_FEEDBACK_VERDICTS,
  type ActionRecord,
  type CourtDecision,
  type DecisionDetail,
  type DecisionRecord,
  type EnvironmentSignal,
  type EnvironmentSummary,
  type FlockSummary,
  type HuddleMessage,
  type HuddleSummary,
  type JudgmentRecord,
  type LearnedSkillRecord,
  type LearnedSkillSummary,
  type LearningRecord,
  type OperationalState,
  type RealmSummary,
  type RealmTrustGrant,
  type ReviewItem,
  type ReviewSummary,
  type SignalHistoryEntry,
  type SkillUsageStat,
  type TingWorkflowSummary,
  type ValkyrieDashboard,
  type ValkyrieResident,
} from '../domain';
import type {
  AutonomyUpdateRequest,
  DecisionListFilters,
  HuddleJoinInput,
  HuddleMessageInput,
  IOdinReviewService,
  IRealmGovernanceService,
  IValkyrieService,
  IValkyrieSkillsService,
  LearningFeedbackInput,
  LearningRevisionInput,
  ReviewDecisionRequest,
  ReviewListFilters,
  SignalHistoryFilters,
  TrustGrantCreate,
} from '../ports';

const UPDATED_AT = '2026-06-03T14:10:00Z';

const environments: EnvironmentSummary[] = [
  {
    id: 'env-k8s-valhalla',
    name: 'Valhalla k8s',
    kind: 'kubernetes',
    health: 'watch',
    flockId: 'flock-k8s',
    topologyNodeIds: ['realm-valhalla', 'cluster-valhalla', 'valkyrie-valhalla-sigrun'],
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
    charter:
      'Keep the Valhalla cluster healthy and boring: catch drift before users do, ' +
      'and only interrupt Jozef for decisions that genuinely need him.',
    signalTaskSeverities: ['warning', 'critical'],
    wakefulness: 'wakeful',
    autonomyMode: 'autonomous',
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
    autonomyMode: 'guarded',
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
    autonomyMode: 'guarded',
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
    wakefulness: 'wakeful',
    autonomyMode: 'autonomous',
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
    learningIds: ['learn-k8s-oom-canary', 'learn-k8s-noisy-probe', 'learn-k8s-eviction-rollback'],
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
    targetFlockId: 'flock-k8s',
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
    targetFlockId: 'flock-printers',
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
    active: true,
    currentScope: 'flock',
    targetScope: 'shared',
    availableScopes: ['private', 'environment', 'domain', 'flock', 'shared'],
    artifactType: 'ravn_skill_tool',
    artifactContent:
      '# skill: k8s_memory_pressure_probe\n\nmetadata:\n  capability: inspect.kubernetes.pod.oomkilled\n  safety_class: read_only\n',
    sourceSignalIds: ['sig-k8s-oom-1', 'sig-k8s-oom-2', 'sig-k8s-oom-3'],
    sourceEvidence: { replayed: 3, predicted: 3 },
    dreamRationale: 'Repeated OOMKilled events preceded queue-depth drift.',
    repetition: 3,
    odinReview: {
      outcome: 'approved',
      approved: true,
      reviewer: 'odin:mock-court',
      rationale: 'Read-only inspection with replay evidence.',
      findings: [],
      requiredForActivation: true,
    },
    history: [
      {
        eventType: 'valkyrie.evolution.built',
        status: 'candidate',
        summary: 'Built skill k8s_memory_pressure_probe',
        observedAt: '2026-06-03T03:18:00Z',
      },
      {
        eventType: 'valkyrie.evolution.activated',
        status: 'canary',
        summary: 'Started canary in Valhalla',
        observedAt: '2026-06-03T03:20:00Z',
      },
    ],
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
    feedback: {
      verdict: 'useful',
      reason: 'Saved two prints in the first week.',
      operatorId: 'human:operator',
      recordedAt: '2026-06-02T08:00:00Z',
    },
  },
  {
    id: 'learn-k8s-eviction-rollback',
    title: 'Aggressive eviction on memory pressure',
    summary: 'Cordon and drain on first eviction caused capacity collapse on smaller clusters.',
    scope: 'flock',
    status: 'rolled_back',
    sourceEnvironmentId: 'env-k8s-valhalla',
    sourceValkyrieId: 'valkyrie-valhalla-sigrun',
    targetFlockId: 'flock-k8s',
    confidence: 0.44,
    evaluation:
      'Negative transfer on Noatun with 4 nodes; drain cascaded. Rolled back after 1 incident.',
    negativeTransferRisk: 'high',
    redaction: 'none',
    promotedTool: 'node_cordon_drain',
    createdAt: '2026-06-02T04:15:00Z',
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
    telemetry: {
      source: 'sleipnir_events',
      verified: true,
      lastObservedAt: UPDATED_AT,
      totals: {
        eventsObserved: 18,
        rawSignalEvents: 6,
        pollsCompleted: 4,
        pollFailures: 1,
        signalsCollected: 42,
        signalsPublished: 31,
        duplicateSignals: 11,
        tasksEnqueued: 7,
        tasksStarted: 6,
        tasksCompleted: 4,
        tasksFailed: 1,
        tasksDropped: 1,
        judgments: 3,
        actions: 2,
        learningEvents: 2,
        dreamCyclesStarted: 1,
        dreamCyclesCompleted: 1,
        dreamCyclesFailed: 0,
        flockMessages: 2,
        wakefulnessChanges: 1,
        toolRequests: 1,
        skillProposals: 1,
      },
      byEnvironment: [
        {
          environmentId: 'env-k8s-valhalla',
          lastObservedAt: UPDATED_AT,
          pollsCompleted: 3,
          pollFailures: 0,
          signalsCollected: 36,
          signalsPublished: 29,
          duplicateSignals: 7,
          tasksEnqueued: 6,
          tasksStarted: 5,
          tasksCompleted: 4,
          tasksFailed: 1,
          tasksDropped: 0,
          judgments: 3,
          actions: 2,
          learningEvents: 2,
          dreamCycles: 1,
        },
        {
          environmentId: 'env-k8s-ymir',
          lastObservedAt: '2026-06-03T14:06:00Z',
          pollsCompleted: 1,
          pollFailures: 1,
          signalsCollected: 6,
          signalsPublished: 2,
          duplicateSignals: 4,
          tasksEnqueued: 1,
          tasksStarted: 1,
          tasksCompleted: 0,
          tasksFailed: 0,
          tasksDropped: 1,
          judgments: 0,
          actions: 0,
          learningEvents: 0,
          dreamCycles: 0,
        },
      ],
      recentPolls: [
        {
          environmentId: 'env-k8s-valhalla',
          sourceId: 'kubernetes-events',
          status: 'completed',
          collected: 14,
          published: 10,
          duplicates: 4,
          tasksEnqueued: 2,
          durationMs: 84,
          observedAt: UPDATED_AT,
        },
        {
          environmentId: 'env-k8s-ymir',
          sourceId: 'kubernetes-events',
          status: 'failed',
          error: 'nats: connection closed',
          observedAt: '2026-06-03T14:06:00Z',
        },
      ],
      recentTasks: [
        {
          environmentId: 'env-k8s-valhalla',
          taskId: 'task-k8s-1',
          title: 'Pod checkout/api: warning',
          status: 'completed',
          outcome: 'success',
          triggeredBy: 'signal:signal.kubernetes.event',
          persona: 'k8s-valkyrie',
          observedAt: UPDATED_AT,
        },
        {
          environmentId: 'env-k8s-ymir',
          taskId: 'task-k8s-2',
          title: 'Job backup/checkpoint: critical',
          status: 'dropped',
          reason: 'queue_full',
          triggeredBy: 'signal:signal.kubernetes.event',
          persona: 'k8s-valkyrie',
          observedAt: '2026-06-03T14:06:00Z',
        },
      ],
      recentOutcomes: [
        {
          environmentId: 'env-k8s-ymir',
          type: 'judgment',
          eventType: 'valkyrie.judgment.proposed',
          taskId: 'task-k8s-1',
          valkyrieId: 'valkyrie-ymir-k8s',
          verdict: 'investigate',
          tier: 'present',
          confidence: 0.84,
          recommendedAction: 'k8s.inspect_pod;k8s.inspect_workload;k8s.inspect_event',
          summary:
            'Persistent ImagePullBackOff on valkyrie-ymir-k8s and stale GHCR image tag require inspection.',
          valid: true,
          observedAt: UPDATED_AT,
        },
        {
          environmentId: 'env-k8s-valhalla',
          type: 'action',
          eventType: 'valkyrie.action.proposed',
          taskId: 'task-k8s-2',
          valkyrieId: 'valkyrie-valhalla-sigrun',
          verdict: 'propose_action',
          tier: 'urgent',
          confidence: 0.76,
          recommendedAction: 'prepare_rollout_remediation',
          summary: 'Prepare a guarded rollout fix for a repeated pull failure.',
          valid: true,
          observedAt: '2026-06-03T14:08:00Z',
        },
      ],
      recentEvents: [
        {
          id: 'event-live-action',
          eventType: 'valkyrie.action.proposed',
          kind: 'action',
          environmentId: 'env-k8s-valhalla',
          valkyrieId: 'valkyrie-valhalla-sigrun',
          valkyrieName: 'Sigrun',
          source: 'ravn:valkyrie:valhalla',
          summary: 'Prepare a guarded rollout fix for a repeated pull failure.',
          urgency: 0.7,
          observedAt: '2026-06-03T14:08:00Z',
          correlationId: 'task-k8s-2',
          details: { action_capability: 'prepare_rollout_remediation' },
        },
        {
          id: 'event-live-task',
          eventType: 'ravn.task.dropped',
          kind: 'task',
          environmentId: 'env-k8s-ymir',
          valkyrieId: 'valkyrie-ymir-k8s',
          valkyrieName: 'Runa',
          source: 'ravn:task-queue',
          summary: 'Job backup/checkpoint: critical',
          urgency: 0.4,
          observedAt: '2026-06-03T14:06:00Z',
          correlationId: 'task-k8s-2',
          details: { reason: 'queue_full' },
        },
        // --- Investigation story: signal → judgment → court decision → action.
        {
          id: 'evt-imagepull-signal',
          eventType: 'signal.kubernetes.event',
          kind: 'signal',
          environmentId: 'env-k8s-valhalla',
          source: 'adapter:kubernetes-events',
          summary: 'Pod ravn-worker-77 stuck in ImagePullBackOff after registry token rollover',
          urgency: 0.6,
          observedAt: '2026-06-03T14:05:00Z',
          correlationId: 'corr-imagepull',
          causationId: '',
          tier: '',
        },
        {
          id: 'evt-imagepull-judgment',
          eventType: 'valkyrie.judgment.proposed',
          kind: 'judgment',
          environmentId: 'env-k8s-valhalla',
          valkyrieId: 'valkyrie-valhalla-sigrun',
          valkyrieName: 'Sigrun',
          source: 'ravn:valkyrie:valhalla',
          summary:
            'Registry token rollover broke image pulls for ravn-worker — checked with ' +
            'registry_token_refresh_check, refreshing the pull secret needs operator sign-off.',
          urgency: 0.7,
          observedAt: '2026-06-03T14:08:20Z',
          correlationId: 'corr-imagepull',
          causationId: 'evt-imagepull-signal',
          tier: 'present',
          details: { skill_name: 'registry_token_refresh_check' },
        },
        {
          id: 'evt-imagepull-court',
          eventType: 'odin.court.decided',
          kind: 'event',
          environmentId: 'env-k8s-valhalla',
          source: 'odin-court',
          summary: 'ODIN approved refresh_pull_secret_and_restart with operator authority',
          urgency: 0.5,
          observedAt: '2026-06-03T14:09:00Z',
          correlationId: 'corr-imagepull',
          causationId: 'evt-imagepull-judgment',
          tier: 'present',
        },
        {
          id: 'evt-imagepull-action',
          eventType: 'valkyrie.action.completed',
          kind: 'action',
          environmentId: 'env-k8s-valhalla',
          valkyrieId: 'valkyrie-valhalla-sigrun',
          valkyrieName: 'Sigrun',
          source: 'ravn:valkyrie:valhalla',
          summary: 'Refreshed the registry pull secret and restarted the ravn-worker rollout',
          urgency: 0.5,
          observedAt: '2026-06-03T14:09:40Z',
          correlationId: 'corr-imagepull',
          causationId: 'evt-imagepull-court',
          tier: 'present',
          details: { action_capability: 'refresh_pull_secret_and_restart' },
        },
        // --- Idle triage story: one ambient judgment, collapsed to a line.
        {
          id: 'evt-idle-judgment',
          eventType: 'valkyrie.judgment.proposed',
          kind: 'judgment',
          environmentId: 'env-k8s-ymir',
          valkyrieId: 'valkyrie-ymir-k8s',
          valkyrieName: 'Runa',
          source: 'ravn:valkyrie:ymir',
          summary: 'Triaged 6 routine signals — nothing needed',
          urgency: 0.1,
          observedAt: '2026-06-03T14:07:00Z',
          correlationId: 'corr-idle-ymir',
          causationId: '',
          tier: 'ambient',
        },
      ],
      recentLearning: [
        {
          id: 'dream-env-k8s-valhalla-1',
          eventType: 'valkyrie.dream.completed',
          environmentId: 'env-k8s-valhalla',
          valkyrieId: 'valkyrie-valhalla-runa',
          dreamId: 'dream-env-k8s-valhalla-1',
          title: 'valkyrie.dream.completed for valkyrie-valhalla-runa',
          status: 'completed',
          proposalsCreated: 1,
          proposalsApplied: 0,
          proposalsDeferred: 1,
          observedAt: '2026-06-03T14:00:00Z',
          summary: 'Dream proposed a guarded rollout inspection skill.',
        },
      ],
      recentToolNeeds: [
        {
          id: 'tool-prepare-rollout',
          eventType: 'valkyrie.action.proposed',
          environmentId: 'env-k8s-valhalla',
          valkyrieId: 'valkyrie-valhalla-sigrun',
          taskId: 'task-k8s-2',
          capability: 'prepare_rollout_remediation',
          status: 'proposed',
          summary: 'Prepare a guarded rollout fix for a repeated pull failure.',
          observedAt: '2026-06-03T14:08:00Z',
        },
      ],
      runtime: [
        {
          environmentId: 'env-k8s-valhalla',
          valkyrieId: 'valkyrie-valhalla-sigrun',
          valkyrieName: 'Sigrun',
          residentPersonality: 'Evidence-first cluster guardian; terse, calm, reversible.',
          sourceCount: 1,
          driveLoopEnabled: true,
          initiativeEnabled: true,
          pollIntervalSeconds: 15,
          observedAt: '2026-06-03T14:00:00Z',
        },
      ],
      llm: {
        status: 'configured',
        model: 'Qwen/Qwen3.6-35B-A3B-FP8',
        reflectionModel: 'Qwen/Qwen3.6-35B-A3B-FP8',
        postSessionReflectionEnabled: true,
        lastObservedAt: '2026-06-03T14:00:00Z',
      },
      gaps: ['No verified cross-cluster flock adoption event observed in this window.'],
    },
    updatedAt: UPDATED_AT,
  };
}

export function createSeedDecisions(): DecisionRecord[] {
  return [
    {
      decisionId: 'decision-imagepull-1',
      environmentId: 'env-k8s-valhalla',
      valkyrieId: 'valkyrie-valhalla-sigrun',
      operationalState: 'investigating',
      tier: 'present',
      wakefulness: 'wakeful',
      confidence: 0.82,
      rationale:
        'ImagePullBackOff started right after the registry token refresh; the rollout ' +
        'needs a refreshed pull secret before pods can converge.',
      recommendedAction: 'refresh_pull_secret_and_restart',
      actionAuthority: 'human_review_required',
      actionCapability: 'k8s.rollout.restart',
      signalRefs: ['sig-hist-imagepull'],
      evidence: [{ event_id: 'sig-hist-imagepull', severity: 'warning' }],
      correlationId: 'corr-imagepull',
      summary: 'Registry token rollover broke image pulls for ravn-worker',
      outcome: '',
      reviewItemId: 'review:court_escalation:seed03',
      decidedAt: '2026-06-03T14:08:20Z',
    },
    {
      decisionId: 'decision-oom-1',
      environmentId: 'env-k8s-valhalla',
      valkyrieId: 'valkyrie-valhalla-runa',
      operationalState: 'using_adopted_learning',
      tier: 'ambient',
      confidence: 0.86,
      rationale: 'Installed learning skill k8s_memory_pressure_probe matches this OOM pattern.',
      recommendedAction: 'inspect_with_adopted_learning',
      actionAuthority: 'autonomous',
      signalRefs: ['sig-hist-oom'],
      evidence: [
        {
          skill_name: 'k8s_memory_pressure_probe',
          capability_name: 'inspect.kubernetes.pod.oomkilled',
        },
      ],
      correlationId: 'corr-oom',
      summary: 'OOMKilled pattern handled with learned probe',
      outcome: 'executed',
      outcomeDetail: 'Probe confirmed memory pressure; capacity note filed.',
      outcomeAt: '2026-06-03T14:03:10Z',
      decidedAt: '2026-06-03T14:02:20Z',
    },
    // The same PersistentVolume re-judged every few minutes: three ambient
    // judgments sharing one correlationId. The console must collapse them
    // into a single ×3 row and must NOT claim they await the operator —
    // `watch` is not a real action, so the inbox gate never fires.
    ...[
      { decisionId: 'decision-pv-3', decidedAt: '2026-06-03T13:58:00Z' },
      { decisionId: 'decision-pv-2', decidedAt: '2026-06-03T13:54:00Z' },
      { decisionId: 'decision-pv-1', decidedAt: '2026-06-03T13:50:00Z' },
    ].map(({ decisionId, decidedAt }) => ({
      decisionId,
      environmentId: 'env-k8s-valhalla',
      valkyrieId: 'valkyrie-valhalla-sigrun',
      operationalState: 'watching',
      tier: 'ambient',
      confidence: 0.71,
      rationale:
        'The claim is Bound and the backing disk reports healthy; re-checking on the ' +
        'ambient cadence until the capacity alert clears.',
      recommendedAction: 'watch',
      actionAuthority: 'human_review_required',
      signalRefs: [],
      evidence: [{ subject: 'pv/media-primary' }],
      correlationId: 'corr-pv-media',
      summary:
        'Valkyrie valkyrie-valhalla-sigrun in env-k8s-valhalla judged PersistentVolume ' +
        'media-primary healthy; capacity holding at 78%',
      outcome: '',
      decidedAt,
    })),
    {
      decisionId: 'decision-triage-1',
      environmentId: 'env-k8s-valhalla',
      valkyrieId: 'valkyrie-valhalla-sigrun',
      operationalState: 'watching',
      tier: 'ambient',
      confidence: 0.66,
      rationale:
        '37 routine signals this window — probe flaps and image GC events. No source ' +
        'shows a rising trend; nothing actionable.',
      recommendedAction: 'none',
      actionAuthority: 'autonomous',
      signalRefs: ['sig-hist-imagepull', 'sig-hist-oom'],
      evidence: [],
      correlationId: 'idle-triage:env-k8s-valhalla',
      summary: 'Idle triage: 37 routine signals',
      outcome: '',
      decidedAt: '2026-06-03T13:45:00Z',
    },
  ];
}

export function createSeedSignalHistory(): SignalHistoryEntry[] {
  return [
    {
      signalId: 'sig-hist-imagepull',
      environmentId: 'env-k8s-valhalla',
      eventType: 'signal.kubernetes.event',
      source: 'adapter:k8s-events',
      subject: 'pod/ravn-worker-77',
      summary: 'ImagePullBackOff after registry token refresh',
      severity: 'warning',
      correlationId: 'corr-imagepull',
      receivedAt: '2026-06-03T14:08:00Z',
    },
    {
      signalId: 'sig-hist-oom',
      environmentId: 'env-k8s-valhalla',
      eventType: 'signal.kubernetes.event',
      source: 'adapter:k8s-events',
      subject: 'deployment/sleipnir-api',
      summary: 'OOMKilled pattern matches learned memory pressure case',
      severity: 'critical',
      correlationId: 'corr-oom',
      receivedAt: '2026-06-03T14:02:00Z',
    },
    {
      signalId: 'sig-hist-email',
      environmentId: 'env-host-jozef',
      eventType: 'signal.email.message',
      source: 'adapter:gmail',
      subject: 'Important contract review',
      summary: 'External sender asks for review before Friday',
      severity: 'notice',
      receivedAt: '2026-06-03T13:55:00Z',
    },
  ];
}

export function createSeedSkillStats(): SkillUsageStat[] {
  return [
    {
      skillName: 'k8s_memory_pressure_probe',
      capability: 'inspect.kubernetes.pod.oomkilled',
      environmentId: 'env-k8s-valhalla',
      uses: 7,
      successes: 6,
      failures: 1,
      lastUsedAt: '2026-06-03T14:02:20Z',
      lastOutcome: 'using_adopted_learning',
      rolledBackAt: '',
    },
    {
      skillName: 'node_cordon_drain',
      capability: 'k8s.node.cordon',
      environmentId: 'env-k8s-valhalla',
      uses: 3,
      successes: 0,
      failures: 3,
      lastUsedAt: '2026-06-02T04:15:00Z',
      lastOutcome: 'adopted_learning_regressed',
      rolledBackAt: '2026-06-02T04:15:00Z',
    },
  ];
}

export function createMockValkyrieService(seed = createSeedValkyrieDashboard()): IValkyrieService {
  const dashboard: ValkyrieDashboard = seed;
  const decisions = createSeedDecisions();
  const signalHistory = createSeedSignalHistory();
  const skillStats = createSeedSkillStats();

  const requireHuddle = (huddleId: string): HuddleSummary => {
    const huddle = dashboard.huddles.find((entry) => entry.id === huddleId);
    if (!huddle) throw new Error(`Huddle ${huddleId} not found`);
    return huddle;
  };
  const replaceHuddle = (next: HuddleSummary) => {
    dashboard.huddles = dashboard.huddles.map((entry) => (entry.id === next.id ? next : entry));
  };
  const requireLearning = (learningId: string): LearningRecord => {
    const learning = dashboard.learnings.find((entry) => entry.id === learningId);
    if (!learning) throw new Error(`Learning ${learningId} not found`);
    return learning;
  };
  const replaceLearning = (next: LearningRecord) => {
    dashboard.learnings = dashboard.learnings.map((entry) => (entry.id === next.id ? next : entry));
  };

  return {
    // A fresh top-level object per fetch so react-query sees mutations made
    // by the write methods below (same reference would short-circuit updates).
    async getDashboard() {
      return { ...dashboard };
    },
    async updateAutonomy(request: AutonomyUpdateRequest) {
      void request.reason;
      dashboard.valkyries = dashboard.valkyries.map((entry) =>
        entry.id === request.valkyrieId ? { ...entry, autonomyMode: request.mode } : entry,
      );
      return { ...dashboard };
    },
    async joinHuddle(request: HuddleJoinInput) {
      const huddle = requireHuddle(request.huddleId);
      const participantId = request.participantId.trim();
      const next: HuddleSummary = {
        ...huddle,
        joined: true,
        joinedParticipantId: participantId,
        joinedDisplayName: request.displayName?.trim() || participantId,
        joinedAction: request.action ?? 'observe',
        participantIds: huddle.participantIds.includes(participantId)
          ? huddle.participantIds
          : [...huddle.participantIds, participantId],
        lastActivityAt: new Date().toISOString(),
      };
      replaceHuddle(next);
      return next;
    },
    async leaveHuddle(huddleId: string) {
      const huddle = requireHuddle(huddleId);
      const participantId = huddle.joinedParticipantId ?? '';
      const next: HuddleSummary = {
        ...huddle,
        joined: false,
        joinedParticipantId: undefined,
        joinedDisplayName: undefined,
        joinedAction: undefined,
        participantIds: huddle.participantIds.filter((entry) => entry !== participantId),
        lastActivityAt: new Date().toISOString(),
      };
      replaceHuddle(next);
      return next;
    },
    async sendHuddleMessage(request: HuddleMessageInput) {
      const huddle = requireHuddle(request.huddleId);
      // Mirror the backend contract exactly: messages need a joined author.
      if (!huddle.joined || !huddle.joinedParticipantId) {
        throw new Error('Join huddle before sending messages');
      }
      const message: HuddleMessage = {
        id: `message-${huddle.messages.length + 1}`,
        huddleId: huddle.id,
        authorId: request.authorId,
        authorName: huddle.joinedDisplayName || request.authorId,
        body: request.body,
        createdAt: new Date().toISOString(),
        directedTo: request.directedTo,
      };
      replaceHuddle({
        ...huddle,
        joined: true,
        messages: [...huddle.messages, message],
        lastActivityAt: message.createdAt,
      });
      return message;
    },
    async listDecisions(filters: DecisionListFilters = {}) {
      let rows = decisions.filter(
        (row) =>
          (!filters.environmentId || row.environmentId === filters.environmentId) &&
          (!filters.valkyrieId || row.valkyrieId === filters.valkyrieId) &&
          (!filters.operationalState || row.operationalState === filters.operationalState),
      );
      rows = [...rows].sort((a, b) => b.decidedAt.localeCompare(a.decidedAt));
      const offset = filters.offset ?? 0;
      const limit = filters.limit ?? 50;
      return { items: rows.slice(offset, offset + limit), total: rows.length, limit, offset };
    },
    async getDecision(decisionId: string): Promise<DecisionDetail | null> {
      const decision = decisions.find((row) => row.decisionId === decisionId);
      if (!decision) return null;
      return {
        decision,
        lineage: {
          signals: signalHistory.filter((signal) => decision.signalRefs.includes(signal.signalId)),
          actions: decision.outcome
            ? [
                {
                  actionId: `act-${decision.decisionId}`,
                  eventId: `evt-act-${decision.decisionId}`,
                  eventType: 'valkyrie.action.executed',
                  status: decision.outcome,
                  environmentId: decision.environmentId,
                  valkyrieId: decision.valkyrieId,
                  capability: decision.actionCapability ?? decision.recommendedAction,
                  actionAuthority: decision.actionAuthority,
                  outcome: decision.outcomeDetail ?? decision.outcome,
                  rationale: decision.rationale,
                  dryRun: false,
                  correlationId: decision.correlationId,
                  summary: decision.summary,
                  observedAt: decision.outcomeAt ?? decision.decidedAt,
                },
              ]
            : [],
          review: null,
        },
      };
    },
    async listSignalHistory(filters: SignalHistoryFilters = {}) {
      let rows = signalHistory.filter(
        (row) =>
          (!filters.environmentId || row.environmentId === filters.environmentId) &&
          (!filters.severity || row.severity === filters.severity),
      );
      rows = [...rows].sort((a, b) => b.receivedAt.localeCompare(a.receivedAt));
      const offset = filters.offset ?? 0;
      const limit = filters.limit ?? 50;
      return { items: rows.slice(offset, offset + limit), total: rows.length, limit, offset };
    },
    async getSkillStats(environmentId?: string) {
      return skillStats.filter((row) => !environmentId || row.environmentId === environmentId);
    },
    async getLearning(learningId: string) {
      const learning = dashboard.learnings.find((entry) => entry.id === learningId);
      return learning ? { ...learning } : null;
    },
    async sendLearningFeedback(request: LearningFeedbackInput) {
      const learning = requireLearning(request.learningId);
      // Mirror the backend contract exactly, including its 422 detail strings.
      if (!LEARNING_FEEDBACK_VERDICTS.some((entry) => entry.verdict === request.verdict)) {
        throw new Error('Unsupported feedback verdict');
      }
      if (request.verdict === 'wrong_tier' && !request.targetScope) {
        throw new Error('targetScope is required for wrong_tier feedback');
      }
      const next: LearningRecord = {
        ...learning,
        feedback: {
          verdict: request.verdict,
          reason: request.reason ?? '',
          operatorId: request.operatorId,
          recordedAt: new Date().toISOString(),
        },
        ...(request.verdict === 'wrong_tier' && request.targetScope
          ? { targetScope: request.targetScope }
          : {}),
      };
      replaceLearning(next);
      return { ...next };
    },
    async reviseLearning(request: LearningRevisionInput) {
      const learning = requireLearning(request.learningId);
      const now = new Date().toISOString();
      const revisedContent = {
        title: request.title ?? learning.title,
        summary: request.summary ?? learning.summary,
        artifactContent: request.content ?? learning.artifactContent,
      };
      const revisionEvent = {
        eventType: 'learning.revised',
        summary: request.reason,
        observedAt: now,
        operatorId: request.operatorId,
        reason: request.reason,
      };
      const installed = learning.status === 'adopted' || learning.status === 'canary';
      if (!installed) {
        // Candidates are editable in place — same record, updated content.
        const next: LearningRecord = {
          ...learning,
          ...revisedContent,
          history: [...(learning.history ?? []), { ...revisionEvent, status: learning.status }],
        };
        replaceLearning(next);
        return { learning: { ...next }, supersededId: '' };
      }
      // Installed learnings are immutable: the revision becomes a NEW
      // superseding candidate while the original stays installed until the
      // candidate passes review.
      const revision =
        dashboard.learnings.filter((entry) => entry.supersedes === learning.id).length + 1;
      const successor: LearningRecord = {
        ...learning,
        ...revisedContent,
        id: `${learning.id}:rev${revision}`,
        status: 'candidate',
        supersedes: learning.id,
        active: false,
        createdAt: now,
        repetition: 1,
        feedback: null,
        odinReview: undefined,
        commandDelivery: undefined,
        history: [{ ...revisionEvent, status: 'candidate' }],
      };
      dashboard.learnings = [successor, ...dashboard.learnings];
      return { learning: { ...successor }, supersededId: learning.id };
    },
  };
}

const SEED_TOOL_CODE = `def run(signal: dict) -> dict:
    payload = signal.get("payload", {})
    is_oom = (
        signal.get("event_type") == "signal.kubernetes.event"
        and payload.get("reason") == "OOMKilled"
        and payload.get("kind") == "Pod"
    )
    return {
        "capability": "inspect.kubernetes.pod.oomkilled",
        "matches": is_oom,
        "observed": {
            "namespace": payload.get("namespace", ""),
            "pod_name": payload.get("name", ""),
        },
    }
`;

const SEED_SKILL_CONTENT = `# skill: valkyrie-inspect-kubernetes-pod-oomkilled

metadata:
  capability: inspect.kubernetes.pod.oomkilled
  safety_class: read_only

## Overview
Detects and inspects Kubernetes Pod OOMKilled events with read-only evidence
gathering and memory-limit correlation.

## Safety Constraints
- Read-only instrumentation only.
- No cluster state mutations.
`;

export function createSeedReviewItems(): ReviewItem[] {
  return [
    {
      itemId: 'review:evolution_build:seed01',
      kind: 'evolution_build',
      requestedAction: 'install',
      environmentId: 'env-ymir',
      valkyrieId: 'valkyrie-ymir-k8s',
      title: 'valkyrie-inspect-kubernetes-pod-oomkilled',
      summary: 'Self-built probe for repeated OOMKilled pods in payments.',
      audience: 'valkyrie',
      flockId: 'flock:k8s-valkyries',
      domain: 'k8s',
      riskClass: 'low',
      safetyClass: 'read_only',
      urgency: 0.6,
      requestedCapability: 'approve',
      evidence: {
        artifact: {
          content: SEED_SKILL_CONTENT,
          tool_code: SEED_TOOL_CODE,
          canary_sample: { kind: 'Pod', reason: 'OOMKilled', namespace: 'payments' },
        },
        review: {
          outcome: 'needs_approval',
          rationale: 'guarded mode records proposals for review',
          findings: ['policy: guarded mode records proposals for review'],
        },
        investigation_prompt:
          'A resident Valkyrie received an environment signal.\n\n' +
          'Environment: env-ymir (k8s)\nSeverity: critical\n' +
          'Object: Pod payments/payments-api-7d9f — reason OOMKilled.\n\n' +
          'Decide whether this is noise, needs watching, or requires action. No ' +
          'installed instrument matches this capability — author one with build_tool ' +
          'that acquires the evidence this investigation lacks.',
        signal_ids: ['sig-oom-payments-1'],
      },
      status: 'pending',
      requestedBy: 'valkyrie-ymir-k8s',
      requestedAt: '2026-06-03T13:58:00Z',
      decidedBy: '',
      decidedAt: '',
      decisionReason: '',
      resolvedAt: '',
      applyOutcome: '',
      applyDetail: '',
    },
    {
      itemId: 'review:skill_promotion:seed02',
      kind: 'skill_promotion',
      requestedAction: 'promote',
      environmentId: 'env-ymir',
      valkyrieId: 'valkyrie-ymir-k8s',
      title: 'proven-disk-pressure-probe',
      summary: 'Promote proven-disk-pressure-probe to environment scope after 4 runs.',
      audience: 'valkyrie',
      flockId: '',
      domain: 'k8s',
      riskClass: 'low',
      safetyClass: 'read_only',
      urgency: 0.4,
      requestedCapability: 'approve',
      evidence: {
        skill_name: 'proven-disk-pressure-probe',
        from_scope: 'private',
        to_scope: 'environment',
        success_count: 4,
        failure_count: 0,
        policy_reason: 'guarded mode records proposals for review',
      },
      status: 'pending',
      requestedBy: 'valkyrie-ymir-k8s',
      requestedAt: '2026-06-03T13:40:00Z',
      decidedBy: '',
      decidedAt: '',
      decisionReason: '',
      resolvedAt: '',
      applyOutcome: '',
      applyDetail: '',
    },
    {
      itemId: 'review:court_escalation:seed03',
      kind: 'court_escalation',
      requestedAction: 'execute_action',
      environmentId: 'env-noatun',
      valkyrieId: 'valkyrie-noatun-k8s',
      title: 'Action draft: k8s.restart_pod',
      summary: 'CrashLoopBackOff storm in checkout; court drafted a restart for review.',
      audience: 'valkyrie',
      flockId: 'flock:k8s-valkyries',
      domain: 'k8s',
      riskClass: 'high',
      safetyClass: 'mutating',
      urgency: 0.9,
      requestedCapability: 'approve',
      evidence: {
        audit_ref: 'odin-court:env-noatun:sig-crashloop-7',
        tier: 'urgent',
        rationale: '2 judgments and 1 action proposal resolved to draft_for_review',
        action: {
          action_id: 'act-restart-checkout',
          capability: 'k8s.restart_pod',
          target: { namespace: 'checkout', pod: 'checkout-api-6f' },
          dry_run: false,
        },
      },
      status: 'pending',
      requestedBy: 'odin-court:env-noatun',
      requestedAt: '2026-06-03T13:20:00Z',
      decidedBy: '',
      decidedAt: '',
      decisionReason: '',
      resolvedAt: '',
      applyOutcome: '',
      applyDetail: '',
    },
    {
      itemId: 'review:flock_learning:seed04',
      kind: 'flock_learning',
      requestedAction: 'adopt',
      environmentId: 'env-noatun',
      valkyrieId: 'valkyrie-noatun-k8s',
      title: 'valkyrie-inspect-kubernetes-pod-oomkilled',
      summary: 'Peer learning from env-ymir held for adoption approval.',
      audience: 'valkyrie',
      flockId: 'flock:k8s-valkyries',
      domain: 'k8s',
      riskClass: 'low',
      safetyClass: 'read_only',
      urgency: 0.5,
      requestedCapability: 'approve',
      evidence: {
        artifact: {
          content: SEED_SKILL_CONTENT,
          tool_code: SEED_TOOL_CODE,
          canary_sample: { kind: 'Pod', reason: 'OOMKilled' },
        },
        review: { outcome: 'needs_approval', rationale: 'guarded peer', findings: [] },
      },
      status: 'applied',
      requestedBy: 'valkyrie-noatun-k8s',
      requestedAt: '2026-06-03T12:10:00Z',
      decidedBy: 'human:operator',
      decidedAt: '2026-06-03T12:32:00Z',
      decisionReason: 'Canary passed on ymir; safe to adopt.',
      resolvedAt: '2026-06-03T12:32:30Z',
      applyOutcome: 'applied',
      applyDetail: 'Installed valkyrie-inspect-kubernetes-pod-oomkilled',
    },
    {
      itemId: 'review:autonomy_change:seed05',
      kind: 'autonomy_change',
      requestedAction: 'set_autonomy_mode',
      environmentId: 'env-ymir',
      valkyrieId: 'valkyrie-ymir-k8s',
      title: 'Set valkyrie-ymir-k8s autonomy to autonomous',
      summary: 'Operator raised autonomy after a clean week.',
      audience: 'valkyrie',
      flockId: '',
      domain: 'k8s',
      riskClass: 'medium',
      safetyClass: 'read_only',
      urgency: 0.4,
      requestedCapability: 'change_autonomy',
      evidence: { mode: 'autonomous', previous_mode: 'guarded' },
      status: 'applied',
      requestedBy: 'human:operator',
      requestedAt: '2026-06-02T09:00:00Z',
      decidedBy: 'human:operator',
      decidedAt: '2026-06-02T09:00:00Z',
      decisionReason: 'Clean week, no regressions.',
      resolvedAt: '2026-06-02T09:00:10Z',
      applyOutcome: 'applied',
      applyDetail: 'autonomy guarded -> autonomous',
    },
  ];
}

export function createMockOdinReviewService(
  seed: ReviewItem[] = createSeedReviewItems(),
): IOdinReviewService {
  const items = new Map(seed.map((item) => [item.itemId, { ...item }]));

  return {
    async listReviews(filters: ReviewListFilters = {}) {
      let rows = [...items.values()].sort((a, b) => b.requestedAt.localeCompare(a.requestedAt));
      if (filters.status) rows = rows.filter((item) => item.status === filters.status);
      if (filters.kind) rows = rows.filter((item) => item.kind === filters.kind);
      if (filters.environmentId) {
        rows = rows.filter((item) => item.environmentId === filters.environmentId);
      }
      if (filters.riskClass) rows = rows.filter((item) => item.riskClass === filters.riskClass);
      if (filters.query) {
        const needle = filters.query.toLocaleLowerCase();
        rows = rows.filter((item) =>
          [
            item.title,
            item.summary,
            item.environmentId,
            item.valkyrieId,
            item.kind,
            item.requestedAction,
          ]
            .join(' ')
            .toLocaleLowerCase()
            .includes(needle),
        );
      }
      if (filters.offset) rows = rows.slice(Math.max(filters.offset, 0));
      if (filters.limit !== undefined) rows = rows.slice(0, Math.max(filters.limit, 0));
      return rows;
    },
    async getReview(itemId: string) {
      return items.get(itemId) ?? null;
    },
    async decideReview(request: ReviewDecisionRequest) {
      const item = items.get(request.itemId);
      if (!item) throw new Error(`Review item not found: ${request.itemId}`);
      if (item.status !== 'pending') {
        throw new Error(`Review item is already ${item.status}`);
      }
      if (request.decision === 'rejected' && !request.reason?.trim()) {
        throw new Error('A reason is required when rejecting a review item');
      }
      const now = new Date().toISOString();
      const decided: ReviewItem = {
        ...item,
        status: request.decision === 'approved' ? 'applied' : 'rejected',
        decidedBy: request.participantId || 'operator',
        decidedAt: now,
        decisionReason: request.reason ?? '',
        resolvedAt: now,
        applyOutcome: 'applied',
        applyDetail:
          request.decision === 'approved'
            ? 'Mock resident applied the decision.'
            : 'Mock resident recorded the rejection.',
      };
      items.set(item.itemId, decided);
      return decided;
    },
    async getSummary(filters = {}): Promise<ReviewSummary> {
      const rows = await this.listReviews({ ...filters, status: 'pending' });
      const countsByStatus: Record<string, number> = {};
      const pendingByKind: Record<string, number> = {};
      const pendingByRisk: Record<string, number> = {};
      const pendingByEnvironment: Record<string, number> = {};
      for (const item of items.values()) {
        countsByStatus[item.status] = (countsByStatus[item.status] ?? 0) + 1;
      }
      for (const item of rows) {
        pendingByKind[item.kind] = (pendingByKind[item.kind] ?? 0) + 1;
        pendingByRisk[item.riskClass] = (pendingByRisk[item.riskClass] ?? 0) + 1;
        pendingByEnvironment[item.environmentId] =
          (pendingByEnvironment[item.environmentId] ?? 0) + 1;
      }
      return {
        pendingTotal: rows.length,
        pendingByKind,
        pendingByRisk,
        pendingByEnvironment,
        countsByStatus,
      };
    },
  };
}

// ---------------------------------------------------------------------------
// Realm governance mocks — realms, trust grants, and Ting builder workflows
// ---------------------------------------------------------------------------

// A realm's slug IS the environment's raw id (see realmSlugForEnvironment):
// env-k8s-valhalla ↔ realm `valhalla`, env-host-jozef ↔ realm `host-jozef`.
export function createSeedRealms(): RealmSummary[] {
  return [
    {
      id: 'realm-valhalla',
      slug: 'valhalla',
      name: 'Valhalla',
      sleipnir_domain: 'valhalla.niuu',
      owner_id: 'human:operator',
      instance_id: 'instance-valhalla',
      autonomy_profile: 'balanced',
      created_at: '2026-05-01T09:00:00Z',
      updated_at: '2026-06-03T12:00:00Z',
    },
    {
      id: 'realm-host-jozef',
      slug: 'host-jozef',
      name: 'Jozef host',
      sleipnir_domain: null,
      owner_id: null,
      instance_id: null,
      autonomy_profile: 'guarded',
      created_at: '2026-05-10T09:00:00Z',
      updated_at: '2026-06-01T08:00:00Z',
    },
    {
      id: 'realm-printer-forge',
      slug: 'printer-forge',
      name: 'Printer forge',
      sleipnir_domain: null,
      owner_id: null,
      instance_id: null,
      autonomy_profile: 'guarded',
      created_at: '2026-05-12T09:00:00Z',
      updated_at: '2026-06-01T08:00:00Z',
    },
  ];
}

export function createSeedTrustGrants(): Record<string, RealmTrustGrant[]> {
  return {
    valhalla: [
      {
        id: 'grant-valhalla-build',
        realm_id: 'realm-valhalla',
        action_class: 'build',
        target: '*',
        level: 2,
        limits: { workflow: 'valkyrie-tool-forge' },
        granted_by: 'human:operator',
        granted_at: '2026-06-02T10:00:00Z',
      },
      {
        id: 'grant-valhalla-deploy',
        realm_id: 'realm-valhalla',
        action_class: 'deploy',
        target: '*',
        level: 1,
        limits: {},
        granted_by: 'human:operator',
        granted_at: '2026-06-02T10:05:00Z',
      },
    ],
    'host-jozef': [],
    'printer-forge': [],
  };
}

export function createSeedToolWorkflows(): TingWorkflowSummary[] {
  return [
    {
      id: 'wf-tool-forge',
      name: 'valkyrie-tool-forge',
      description: 'Guarded tool build with canary and review gates.',
      version: '1.2.0',
      tags: ['tool-builder', 'valkyrie'],
    },
    {
      id: 'wf-tool-forge-fast',
      name: 'valkyrie-tool-forge-fast',
      description: 'Single-pass tool build for trusted realms.',
      version: '0.4.0',
      tags: ['tool-builder'],
    },
    {
      id: 'wf-release-train',
      name: 'release-train',
      description: 'Weekly release pipeline — not a tool builder.',
      version: '3.0.0',
      tags: ['release'],
    },
  ];
}

export interface RealmGovernanceSeed {
  realms?: RealmSummary[];
  grants?: Record<string, RealmTrustGrant[]>;
  workflows?: TingWorkflowSummary[];
}

export function createMockRealmGovernanceService(
  seed: RealmGovernanceSeed = {},
): IRealmGovernanceService {
  const realms = (seed.realms ?? createSeedRealms()).map((realm) => ({ ...realm }));
  const grants = new Map<string, RealmTrustGrant[]>(
    Object.entries(seed.grants ?? createSeedTrustGrants()).map(([slug, entries]) => [
      slug,
      entries.map((entry) => ({ ...entry })),
    ]),
  );
  const workflows = (seed.workflows ?? createSeedToolWorkflows()).map((workflow) => ({
    ...workflow,
  }));

  function requireRealm(slug: string): RealmSummary {
    const realm = realms.find((entry) => entry.slug === slug);
    if (!realm) throw new Error(`Realm not found: ${slug}`);
    return realm;
  }

  return {
    async listRealms() {
      return realms.map((realm) => ({ ...realm }));
    },
    async listTrustGrants(slug: string) {
      requireRealm(slug);
      return (grants.get(slug) ?? []).map((grant) => ({ ...grant }));
    },
    async createTrustGrant(slug: string, request: TrustGrantCreate) {
      const realm = requireRealm(slug);
      const grant: RealmTrustGrant = {
        id: `grant-${slug}-${(grants.get(slug) ?? []).length + 1}`,
        realm_id: realm.id,
        action_class: request.action_class,
        target: request.target,
        level: request.level,
        limits: { ...request.limits },
        granted_by: request.granted_by ?? null,
        granted_at: new Date().toISOString(),
      };
      grants.set(slug, [...(grants.get(slug) ?? []), grant]);
      return { ...grant };
    },
    async listWorkflows() {
      return workflows.map((workflow) => ({ ...workflow }));
    },
  };
}

// ---------------------------------------------------------------------------
// Learned skills — the artifacts behind "handled with a learned skill"
// judgments, matching GET /skills and GET /skills/{name}
// ---------------------------------------------------------------------------

const SEED_TEST_CODE = `def test_matches_oomkilled_pod():
    signal = {
        "event_type": "signal.kubernetes.event",
        "payload": {"reason": "OOMKilled", "kind": "Pod", "namespace": "payments", "name": "api-1"},
    }
    result = run(signal)
    assert result["matches"] is True
    assert result["observed"]["namespace"] == "payments"


def test_ignores_other_events():
    assert run({"event_type": "signal.email.message", "payload": {}})["matches"] is False
`;

export function createSeedLearnedSkills(): LearnedSkillRecord[] {
  return [
    {
      skillName: 'k8s_memory_pressure_probe',
      environmentId: 'env-k8s-valhalla',
      valkyrieId: 'valkyrie-valhalla-runa',
      description:
        'Read-only probe that inspects OOMKilled pods and correlates them with memory limits.',
      learningId: 'learning-oom-probe',
      adoptedAt: '2026-06-01T09:00:00Z',
      observedAt: '2026-07-15T10:00:00Z',
      hasCode: true,
      content: SEED_SKILL_CONTENT,
      toolCode: SEED_TOOL_CODE,
      testCode: SEED_TEST_CODE,
      requirements: ['kubernetes>=29.0.0'],
      manifest: { capability: 'inspect.kubernetes.pod.oomkilled', safety_class: 'read_only' },
    },
    {
      skillName: 'registry_token_refresh_check',
      environmentId: 'env-k8s-valhalla',
      valkyrieId: 'valkyrie-valhalla-sigrun',
      description: 'Checks image pull-secret freshness after a registry token rollover.',
      learningId: 'learning-registry-check',
      adoptedAt: '2026-05-28T16:30:00Z',
      observedAt: '2026-07-15T10:00:00Z',
      hasCode: false,
      content:
        '# skill: registry-token-refresh-check\n\n## Overview\nVerify image pull secrets ' +
        'still authenticate after a registry token rollover before pods retry.',
      toolCode: '',
      testCode: '',
      requirements: [],
      manifest: {},
    },
  ];
}

function toLearnedSkillSummary(record: LearnedSkillRecord): LearnedSkillSummary {
  return {
    skillName: record.skillName,
    environmentId: record.environmentId,
    valkyrieId: record.valkyrieId,
    description: record.description,
    learningId: record.learningId,
    adoptedAt: record.adoptedAt,
    observedAt: record.observedAt,
    hasCode: record.hasCode,
  };
}

export function createMockValkyrieSkillsService(
  seed: LearnedSkillRecord[] = createSeedLearnedSkills(),
): IValkyrieSkillsService {
  const skills = seed.map((record) => ({ ...record }));

  return {
    async listSkills(environmentId: string) {
      return skills
        .filter((record) => !environmentId || record.environmentId === environmentId)
        .sort((a, b) => b.adoptedAt.localeCompare(a.adoptedAt))
        .map(toLearnedSkillSummary);
    },
    async getSkill(environmentId: string, name: string) {
      const found = skills.find(
        (record) =>
          record.skillName === name && (!environmentId || record.environmentId === environmentId),
      );
      return found ? { ...found } : null;
    },
  };
}
