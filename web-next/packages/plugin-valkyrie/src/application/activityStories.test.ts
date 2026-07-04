import { describe, expect, it } from 'vitest';
import type { ValkyrieEventTelemetry } from '../domain';
import {
  DEFAULT_STORY_FILTERS,
  actionStatus,
  eventSkillName,
  filterActivityStories,
  groupActivityStories,
  isRoutineStory,
  matchesStoryFilters,
  orderStoryEvents,
} from './activityStories';

function event(
  overrides: Partial<ValkyrieEventTelemetry> & { id: string },
): ValkyrieEventTelemetry {
  return {
    eventType: 'signal.kubernetes.event',
    kind: 'signal',
    environmentId: 'env-k8s-valhalla',
    summary: `summary for ${overrides.id}`,
    observedAt: '2026-06-03T14:00:00Z',
    ...overrides,
  };
}

const investigation = [
  event({
    id: 'sig-1',
    kind: 'signal',
    correlationId: 'corr-1',
    observedAt: '2026-06-03T14:00:00Z',
    summary: 'Pod stuck in ImagePullBackOff',
  }),
  event({
    id: 'judge-1',
    kind: 'judgment',
    eventType: 'valkyrie.judgment.proposed',
    correlationId: 'corr-1',
    causationId: 'sig-1',
    tier: 'present',
    observedAt: '2026-06-03T14:01:00Z',
    summary: 'Registry token rollover broke image pulls',
  }),
  event({
    id: 'act-1',
    kind: 'action',
    eventType: 'valkyrie.action.completed',
    correlationId: 'corr-1',
    causationId: 'judge-1',
    observedAt: '2026-06-03T14:02:00Z',
    summary: 'Refreshed the pull secret',
  }),
];

const idleTriage = [
  event({
    id: 'idle-judge',
    kind: 'judgment',
    eventType: 'valkyrie.judgment.proposed',
    environmentId: 'env-k8s-ymir',
    correlationId: 'corr-idle',
    tier: 'ambient',
    observedAt: '2026-06-03T14:03:00Z',
    summary: 'Triaged 6 routine signals — nothing needed',
  }),
];

describe('groupActivityStories', () => {
  it('groups events by correlation id, newest story first', () => {
    const stories = groupActivityStories([...idleTriage, ...investigation]);
    expect(stories.map((story) => story.correlationId)).toEqual(['corr-idle', 'corr-1']);
    expect(stories[1]!.events.map((entry) => entry.id)).toEqual(['sig-1', 'judge-1', 'act-1']);
    expect(stories[1]!.latestAt).toBe('2026-06-03T14:02:00Z');
  });

  it('turns uncorrelated events into singleton stories', () => {
    const stories = groupActivityStories([event({ id: 'lonely', correlationId: '' })]);
    expect(stories).toHaveLength(1);
    expect(stories[0]!.correlationId).toBe('event:lonely');
    expect(stories[0]!.kind).toBe('investigation');
  });

  it('classifies ambient and observational judgments as idle triage', () => {
    for (const tier of ['ambient', 'observational', 'Ambient']) {
      const stories = groupActivityStories([
        event({ id: 'j', kind: 'judgment', correlationId: 'c', tier }),
      ]);
      expect(stories[0]!.kind).toBe('idle_triage');
      expect(stories[0]!.tier).toBe(tier.toLowerCase());
    }
  });

  it('keeps a story with actions an investigation even when the judgment is ambient', () => {
    const stories = groupActivityStories([
      event({ id: 'j', kind: 'judgment', correlationId: 'c', tier: 'ambient' }),
      event({ id: 'a', kind: 'action', eventType: 'valkyrie.action.proposed', correlationId: 'c' }),
    ]);
    expect(stories[0]!.kind).toBe('investigation');
  });

  it('derives the triage headline count from the story signals', () => {
    const stories = groupActivityStories([
      event({ id: 's1', kind: 'signal', correlationId: 'c', observedAt: '2026-06-03T14:00:00Z' }),
      event({ id: 's2', kind: 'signal', correlationId: 'c', observedAt: '2026-06-03T14:00:10Z' }),
      event({
        id: 'j',
        kind: 'judgment',
        correlationId: 'c',
        tier: 'ambient',
        observedAt: '2026-06-03T14:01:00Z',
        summary: 'quiet window',
      }),
    ]);
    expect(stories[0]!.headline).toBe('Triaged 2 routine signals — nothing needed');
  });

  it('uses the singular form for a single triaged signal', () => {
    const stories = groupActivityStories([
      event({ id: 's1', kind: 'signal', correlationId: 'c' }),
      event({ id: 'j', kind: 'judgment', correlationId: 'c', tier: 'ambient' }),
    ]);
    expect(stories[0]!.headline).toBe('Triaged 1 routine signal — nothing needed');
  });

  it('reuses the judgment summary for signal-less triage stories', () => {
    expect(groupActivityStories(idleTriage)[0]!.headline).toBe(
      'Triaged 6 routine signals — nothing needed',
    );
  });

  it('falls back to a generic triage line when the judgment has no summary', () => {
    const stories = groupActivityStories([
      event({ id: 'j', kind: 'judgment', correlationId: 'c', tier: 'ambient', summary: '' }),
    ]);
    expect(stories[0]!.headline).toBe('Routine triage — nothing needed');
  });

  it('headlines investigations with the judgment summary, then signal, then latest event', () => {
    expect(groupActivityStories(investigation)[0]!.headline).toBe(
      'Registry token rollover broke image pulls',
    );
    const noJudgment = groupActivityStories([
      event({ id: 'sig', kind: 'signal', correlationId: 'c', summary: 'disk pressure rising' }),
    ]);
    expect(noJudgment[0]!.headline).toBe('disk pressure rising');
    const actionOnly = groupActivityStories([
      event({
        id: 'act',
        kind: 'action',
        eventType: 'valkyrie.action.proposed',
        correlationId: 'c',
        summary: 'prepared a rollout fix',
      }),
    ]);
    expect(actionOnly[0]!.headline).toBe('prepared a rollout fix');
  });

  it('collects every environment the story touches', () => {
    const stories = groupActivityStories([
      event({ id: 'a', correlationId: 'c', environmentId: 'env-k8s-valhalla' }),
      event({ id: 'b', correlationId: 'c', environmentId: 'env-k8s-ymir' }),
    ]);
    expect(stories[0]!.environmentIds).toEqual(['env-k8s-valhalla', 'env-k8s-ymir']);
  });
});

describe('orderStoryEvents', () => {
  it('orders cause before effect even when timestamps disagree', () => {
    const ordered = orderStoryEvents([
      event({ id: 'effect', causationId: 'cause', observedAt: '2026-06-03T14:00:00Z' }),
      event({ id: 'cause', observedAt: '2026-06-03T14:00:05Z' }),
    ]);
    expect(ordered.map((entry) => entry.id)).toEqual(['cause', 'effect']);
  });

  it('breaks timestamp ties in narrative order: signal, judgment, other, action', () => {
    const at = '2026-06-03T14:00:00Z';
    const ordered = orderStoryEvents([
      event({ id: 'act', kind: 'action', eventType: 'valkyrie.action.proposed', observedAt: at }),
      event({ id: 'court', kind: 'event', eventType: 'odin.court.decided', observedAt: at }),
      event({ id: 'judge', kind: 'judgment', observedAt: at }),
      event({ id: 'sig', kind: 'signal', observedAt: at }),
    ]);
    expect(ordered.map((entry) => entry.id)).toEqual(['sig', 'judge', 'court', 'act']);
  });

  it('ignores causation ids pointing outside the story', () => {
    const ordered = orderStoryEvents([
      event({ id: 'b', causationId: 'not-here', observedAt: '2026-06-03T14:00:00Z' }),
      event({ id: 'c', observedAt: '2026-06-03T14:00:05Z' }),
    ]);
    expect(ordered.map((entry) => entry.id)).toEqual(['b', 'c']);
  });

  it('keeps every event when causation forms a cycle', () => {
    const ordered = orderStoryEvents([
      event({ id: 'a', causationId: 'b', observedAt: '2026-06-03T14:00:00Z' }),
      event({ id: 'b', causationId: 'a', observedAt: '2026-06-03T14:00:05Z' }),
    ]);
    expect(ordered).toHaveLength(2);
  });
});

describe('story filters', () => {
  const stories = groupActivityStories([...investigation, ...idleTriage]);

  it('hides routine triage by default', () => {
    const visible = filterActivityStories(stories, DEFAULT_STORY_FILTERS);
    expect(visible.map((story) => story.correlationId)).toEqual(['corr-1']);
  });

  it('shows routine triage when opted in', () => {
    const visible = filterActivityStories(stories, {
      ...DEFAULT_STORY_FILTERS,
      showRoutine: true,
    });
    expect(visible.map((story) => story.correlationId)).toEqual(['corr-idle', 'corr-1']);
  });

  it('filters by environment membership', () => {
    const filters = { ...DEFAULT_STORY_FILTERS, showRoutine: true, environmentId: 'env-k8s-ymir' };
    expect(filterActivityStories(stories, filters).map((story) => story.correlationId)).toEqual([
      'corr-idle',
    ]);
  });

  it('keeps only stories with actions when actionsOnly is set', () => {
    const filters = { ...DEFAULT_STORY_FILTERS, showRoutine: true, actionsOnly: true };
    expect(filterActivityStories(stories, filters).map((story) => story.correlationId)).toEqual([
      'corr-1',
    ]);
  });

  it('treats ambient-tier stories as routine even without the idle-triage shape', () => {
    const ambientWithAction = groupActivityStories([
      event({ id: 'j', kind: 'judgment', correlationId: 'c', tier: 'ambient' }),
      event({ id: 'a', kind: 'action', eventType: 'valkyrie.action.proposed', correlationId: 'c' }),
    ])[0]!;
    expect(ambientWithAction.kind).toBe('investigation');
    expect(isRoutineStory(ambientWithAction)).toBe(true);
    expect(matchesStoryFilters(ambientWithAction, DEFAULT_STORY_FILTERS)).toBe(false);
  });
});

describe('eventSkillName', () => {
  const known = ['k8s_memory_pressure_probe', 'registry_token_refresh_check'];

  it('prefers the explicit skill_name detail', () => {
    expect(
      eventSkillName(
        { summary: 'anything', details: { skill_name: 'registry_token_refresh_check' } },
        known,
      ),
    ).toBe('registry_token_refresh_check');
  });

  it('falls back to a known skill mentioned in the summary', () => {
    expect(
      eventSkillName({ summary: 'handled via k8s_memory_pressure_probe', details: {} }, known),
    ).toBe('k8s_memory_pressure_probe');
  });

  it('returns empty when nothing matches', () => {
    expect(eventSkillName({ summary: 'no skills here' }, known)).toBe('');
    expect(eventSkillName({ summary: 'no skills here' }, [])).toBe('');
  });
});

describe('actionStatus', () => {
  it('reads the lifecycle from the event type suffix', () => {
    expect(actionStatus({ kind: 'action', eventType: 'valkyrie.action.completed' })).toBe(
      'completed',
    );
    expect(actionStatus({ kind: 'action', eventType: 'valkyrie.action.failed' })).toBe('failed');
    expect(actionStatus({ kind: 'action', eventType: 'valkyrie.action.proposed' })).toBe(
      'proposed',
    );
  });

  it('returns empty for non-action events', () => {
    expect(actionStatus({ kind: 'judgment', eventType: 'valkyrie.judgment.proposed' })).toBe('');
  });
});
