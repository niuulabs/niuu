import type {
  ActionRecord,
  CourtDecision,
  EnvironmentSignal,
  FlockSummary,
  HuddleSummary,
  JudgmentRecord,
  LearningRecord,
  OperationalState,
  ValkyrieDashboard,
  ValkyrieResident,
} from '../domain';

export interface EnvironmentSlice {
  valkyries: ValkyrieResident[];
  signals: EnvironmentSignal[];
  operationalStates: OperationalState[];
  judgments: JudgmentRecord[];
  courtDecisions: CourtDecision[];
  actions: ActionRecord[];
  huddles: HuddleSummary[];
  learnings: LearningRecord[];
  flock: FlockSummary | null;
}

export function selectEnvironmentSlice(
  dashboard: ValkyrieDashboard,
  environmentId: string,
): EnvironmentSlice {
  const valkyries = dashboard.valkyries.filter((entry) => entry.environmentId === environmentId);
  const valkyrieIds = new Set(valkyries.map((entry) => entry.id));
  const flock =
    dashboard.flocks.find((entry) => entry.environmentIds.includes(environmentId)) ?? null;
  return {
    valkyries,
    signals: dashboard.signals.filter((entry) => entry.environmentId === environmentId),
    operationalStates: dashboard.operationalStates.filter(
      (entry) => entry.environmentId === environmentId,
    ),
    judgments: dashboard.judgments.filter((entry) => entry.environmentId === environmentId),
    courtDecisions: dashboard.courtDecisions.filter(
      (entry) => entry.environmentId === environmentId,
    ),
    actions: dashboard.actions.filter((entry) => entry.environmentId === environmentId),
    huddles: dashboard.huddles.filter((entry) => entry.environmentId === environmentId),
    learnings: dashboard.learnings.filter(
      (entry) =>
        entry.sourceEnvironmentId === environmentId ||
        (entry.targetFlockId !== undefined && entry.targetFlockId === flock?.id) ||
        valkyrieIds.has(entry.sourceValkyrieId),
    ),
    flock,
  };
}

export function selectFlockLearnings(
  dashboard: ValkyrieDashboard,
  flockId: string,
): LearningRecord[] {
  const flock = dashboard.flocks.find((entry) => entry.id === flockId);
  if (!flock) return [];
  const learningIds = new Set(flock.learningIds);
  return dashboard.learnings.filter(
    (entry) => entry.targetFlockId === flockId || learningIds.has(entry.id),
  );
}
