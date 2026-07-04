import type { ValkyrieEventTelemetry } from '../domain';

/**
 * Correlation-grouped activity stories.
 *
 * Platform events carry `correlationId`/`causationId` forming causal chains:
 * signal → judgment → (review/court decision) → action → outcome. The
 * activity page renders one story per correlation instead of a flat event
 * tail. Everything in this module is pure so it can be tested without React.
 */

export type ActivityStoryKind = 'idle_triage' | 'investigation';

export interface ActivityStory {
  /** Correlation id, or `event:<id>` for uncorrelated singleton events. */
  correlationId: string;
  kind: ActivityStoryKind;
  /** Events in causal order (cause before effect, then oldest first). */
  events: ValkyrieEventTelemetry[];
  /** Every environment the story touches, first one is the primary. */
  environmentIds: string[];
  /** Timestamp of the newest event — stories sort by this, newest first. */
  latestAt: string;
  /** The story's judgment tier ('' when no judgment carries one). */
  tier: string;
  headline: string;
  signalCount: number;
  actionCount: number;
}

export interface ActivityStoryFilters {
  /** '' shows every environment. */
  environmentId: string;
  /** Ambient/observational triage is hidden until the operator opts in. */
  showRoutine: boolean;
  /** Only stories that contain at least one action event. */
  actionsOnly: boolean;
}

export const DEFAULT_STORY_FILTERS: ActivityStoryFilters = {
  environmentId: '',
  showRoutine: false,
  actionsOnly: false,
};

const ROUTINE_TIERS = new Set(['ambient', 'observational']);

/** Kind rank used only to break timestamp ties into narrative order. */
const KIND_RANK: Partial<Record<ValkyrieEventTelemetry['kind'], number>> = {
  signal: 0,
  judgment: 1,
  action: 3,
};

function kindRank(event: ValkyrieEventTelemetry): number {
  return KIND_RANK[event.kind] ?? 2;
}

function compareCausal(a: ValkyrieEventTelemetry, b: ValkyrieEventTelemetry): number {
  return (
    a.observedAt.localeCompare(b.observedAt) ||
    kindRank(a) - kindRank(b) ||
    a.id.localeCompare(b.id)
  );
}

/**
 * Order a story's events cause-before-effect. Causation edges win; ties and
 * unlinked events fall back to observedAt, then narrative kind order
 * (signal → judgment → other → action).
 */
export function orderStoryEvents(events: ValkyrieEventTelemetry[]): ValkyrieEventTelemetry[] {
  const ids = new Set(events.map((event) => event.id));
  const blockedBy = new Map<string, string>();
  for (const event of events) {
    if (event.causationId && ids.has(event.causationId) && event.causationId !== event.id) {
      blockedBy.set(event.id, event.causationId);
    }
  }
  const remaining = [...events].sort(compareCausal);
  const ordered: ValkyrieEventTelemetry[] = [];
  const placed = new Set<string>();
  while (remaining.length > 0) {
    const index = remaining.findIndex((event) => {
      const cause = blockedBy.get(event.id);
      return !cause || placed.has(cause);
    });
    // A causation cycle would leave every event blocked; fall back to the
    // timestamp order instead of dropping events.
    const next = remaining.splice(index === -1 ? 0 : index, 1)[0]!;
    ordered.push(next);
    placed.add(next.id);
  }
  return ordered;
}

function storyTier(events: ValkyrieEventTelemetry[]): string {
  for (const event of events) {
    if (event.kind === 'judgment' && event.tier) return event.tier.toLowerCase();
  }
  return '';
}

function isIdleTriage(events: ValkyrieEventTelemetry[]): boolean {
  const judgments = events.filter((event) => event.kind === 'judgment');
  if (judgments.length === 0) return false;
  if (events.some((event) => event.kind === 'action')) return false;
  return judgments.every((event) => ROUTINE_TIERS.has((event.tier ?? '').toLowerCase()));
}

function storyHeadline(
  kind: ActivityStoryKind,
  events: ValkyrieEventTelemetry[],
  signalCount: number,
): string {
  const judgment = events.find((event) => event.kind === 'judgment');
  if (kind === 'idle_triage') {
    if (signalCount > 0) {
      return `Triaged ${signalCount} routine signal${signalCount === 1 ? '' : 's'} — nothing needed`;
    }
    return judgment?.summary || 'Routine triage — nothing needed';
  }
  const firstSignal = events.find((event) => event.kind === 'signal');
  const latest = events[events.length - 1];
  return judgment?.summary || firstSignal?.summary || latest?.summary || '';
}

function buildStory(correlationId: string, rawEvents: ValkyrieEventTelemetry[]): ActivityStory {
  const events = orderStoryEvents(rawEvents);
  const signalCount = events.filter((event) => event.kind === 'signal').length;
  const actionCount = events.filter((event) => event.kind === 'action').length;
  const kind: ActivityStoryKind = isIdleTriage(events) ? 'idle_triage' : 'investigation';
  const environmentIds = [...new Set(events.map((event) => event.environmentId))];
  const latestAt = events.reduce(
    (latest, event) => (event.observedAt > latest ? event.observedAt : latest),
    '',
  );
  return {
    correlationId,
    kind,
    events,
    environmentIds,
    latestAt,
    tier: storyTier(events),
    headline: storyHeadline(kind, events, signalCount),
    signalCount,
    actionCount,
  };
}

/** Group raw telemetry events into stories, newest activity first. */
export function groupActivityStories(events: ValkyrieEventTelemetry[]): ActivityStory[] {
  const byCorrelation = new Map<string, ValkyrieEventTelemetry[]>();
  for (const event of events) {
    const key = event.correlationId || `event:${event.id}`;
    const bucket = byCorrelation.get(key);
    if (bucket) {
      bucket.push(event);
      continue;
    }
    byCorrelation.set(key, [event]);
  }
  return [...byCorrelation.entries()]
    .map(([correlationId, grouped]) => buildStory(correlationId, grouped))
    .sort(
      (a, b) =>
        b.latestAt.localeCompare(a.latestAt) || a.correlationId.localeCompare(b.correlationId),
    );
}

/** Routine (ambient/observational) stories are hidden unless opted in. */
export function isRoutineStory(story: ActivityStory): boolean {
  return story.kind === 'idle_triage' || ROUTINE_TIERS.has(story.tier);
}

export function matchesStoryFilters(story: ActivityStory, filters: ActivityStoryFilters): boolean {
  if (!filters.showRoutine && isRoutineStory(story)) return false;
  if (filters.environmentId && !story.environmentIds.includes(filters.environmentId)) return false;
  if (filters.actionsOnly && story.actionCount === 0) return false;
  return true;
}

export function filterActivityStories(
  stories: ActivityStory[],
  filters: ActivityStoryFilters,
): ActivityStory[] {
  return stories.filter((story) => matchesStoryFilters(story, filters));
}

/**
 * The learned skill an event references: the explicit `skill_name` detail
 * wins; otherwise the first known skill name mentioned in the summary.
 */
export function eventSkillName(
  event: Pick<ValkyrieEventTelemetry, 'summary' | 'details'>,
  knownSkillNames: readonly string[],
): string {
  const explicit = event.details?.['skill_name'];
  if (typeof explicit === 'string' && explicit) return explicit;
  return knownSkillNames.find((name) => name && event.summary.includes(name)) ?? '';
}

/** Action lifecycle from the event type suffix ('completed', 'failed', …). */
export function actionStatus(event: Pick<ValkyrieEventTelemetry, 'kind' | 'eventType'>): string {
  if (event.kind !== 'action') return '';
  const suffix = event.eventType.split('.').pop() ?? '';
  return suffix === 'action' ? '' : suffix;
}
