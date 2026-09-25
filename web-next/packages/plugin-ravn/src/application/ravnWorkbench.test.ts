import { describe, expect, it } from 'vitest';
import {
  defaultRavn,
  describeCondition,
  failingCondition,
  filterCounts,
  fleetUsage,
  formatTokens,
  formatUsd,
  groupRavnsBy,
  matchesFilter,
  matchesQuery,
  needsAttention,
  ravnLifeState,
  ravnReasonLine,
  ravnTarget,
} from './ravnWorkbench';
import { failedRavn, makeRavn } from '../testing/fixtures';

describe('ravnLifeState', () => {
  it.each([
    ['active', 'running'],
    ['pending', 'starting'],
    ['deploying', 'starting'],
    ['deleting', 'removing'],
    ['suspended', 'suspended'],
    ['failed', 'failed'],
  ] as const)('reads observed %s as %s', (observedState, state) => {
    expect(ravnLifeState(makeRavn({ observedState }))).toBe(state);
  });

  it.each([
    ['active', 'running'],
    ['idle', 'idle'],
    ['suspended', 'suspended'],
    ['failed', 'failed'],
    ['completed', 'stopped'],
  ] as const)('falls to status %s for unmanaged ravens', (status, state) => {
    expect(ravnLifeState(makeRavn({ observedState: undefined, status }))).toBe(state);
  });
});

describe('attention and reasons', () => {
  it('flags failed ravens and names the failing condition', () => {
    const ravn = failedRavn();
    expect(needsAttention(ravn)).toBe(true);
    expect(failingCondition(ravn)?.reason).toBe('ReconcileFailed');
    expect(ravnReasonLine(ravn)).toBe('Backend not ready · reconcile failed');
  });

  it('flags a running ravn only when a check is false', () => {
    expect(needsAttention(makeRavn())).toBe(false);
    const degraded = makeRavn({
      conditions: [
        { type: 'MeshJoined', status: 'false', reason: '', message: '', lastTransitionAt: '' },
      ],
    });
    expect(needsAttention(degraded)).toBe(true);
    expect(ravnReasonLine(degraded)).toBe('Mesh joined');
  });

  it('does not flag quiet states', () => {
    expect(needsAttention(makeRavn({ observedState: 'suspended' }))).toBe(false);
    expect(ravnReasonLine(makeRavn())).toBeNull();
  });

  it('says so when a failed ravn reports no condition', () => {
    expect(ravnReasonLine(failedRavn({ conditions: [] }))).toBe('No failing condition reported');
  });

  it('describes progress while starting or removing', () => {
    expect(ravnReasonLine(makeRavn({ observedState: 'deploying' }))).toBe(
      'Deploying on Local Forge…',
    );
    expect(ravnReasonLine(makeRavn({ observedState: 'deleting' }))).toBe(
      'Removing from Local Forge…',
    );
  });

  it('describes condition types that are not readiness checks', () => {
    expect(describeCondition({ type: 'image_pull', reason: '' })).toBe('Image pull');
  });
});

describe('ravnTarget', () => {
  it('prefers the target name, then slug, location and backend', () => {
    expect(ravnTarget(makeRavn())).toBe('Local Forge');
    expect(ravnTarget(makeRavn({ instanceName: '', instanceSlug: 'local' }))).toBe('local');
    expect(ravnTarget(makeRavn({ instanceName: '', location: 'eu-west-1' }))).toBe('eu-west-1');
    expect(ravnTarget(makeRavn({ instanceName: '', backend: 'openshell' }))).toBe('openshell');
    expect(ravnTarget(makeRavn({ instanceName: '', backend: undefined }))).toBe('unassigned');
  });
});

describe('filtering', () => {
  const fleet = [
    makeRavn({ id: 'a', residentName: 'Muninn' }),
    failedRavn({ id: 'b', residentName: 'Proof', engine: 'openclaw' }),
    makeRavn({ id: 'c', residentName: 'Fulla', observedState: 'suspended' }),
  ];

  it('counts every pill', () => {
    expect(filterCounts(fleet)).toEqual({ all: 3, attention: 1, running: 1, suspended: 1 });
  });

  it('matches each filter', () => {
    expect(fleet.filter((ravn) => matchesFilter(ravn, 'all'))).toHaveLength(3);
    expect(fleet.filter((ravn) => matchesFilter(ravn, 'attention')).map((r) => r.id)).toEqual([
      'b',
    ]);
    expect(fleet.filter((ravn) => matchesFilter(ravn, 'running')).map((r) => r.id)).toEqual(['a']);
    expect(fleet.filter((ravn) => matchesFilter(ravn, 'suspended')).map((r) => r.id)).toEqual([
      'c',
    ]);
  });

  it('searches name, engine, model and target', () => {
    expect(matchesQuery(fleet[1]!, 'openclaw')).toBe(true);
    expect(matchesQuery(fleet[0]!, 'forge')).toBe(true);
    expect(matchesQuery(fleet[0]!, '  ')).toBe(true);
    expect(matchesQuery(fleet[0]!, 'nothing')).toBe(false);
  });
});

describe('groupRavnsBy', () => {
  it('orders state groups by urgency and puts attention first inside a group', () => {
    const groups = groupRavnsBy(
      [
        makeRavn({ id: 'a', residentName: 'Zed' }),
        makeRavn({ id: 'b', residentName: 'Amy', observedState: 'suspended' }),
        failedRavn({ id: 'c', residentName: 'Bob' }),
      ],
      'state',
    );
    expect(groups.map((group) => group.label)).toEqual(['Failed', 'Running', 'Suspended']);
  });

  it('groups by target and engine alphabetically', () => {
    const fleet = [
      makeRavn({ id: 'a', instanceId: 'x', instanceName: 'valhalla', engine: 'hermes' }),
      makeRavn({ id: 'b', instanceId: 'y', instanceName: 'Local Forge', engine: 'ravn' }),
    ];
    expect(groupRavnsBy(fleet, 'target').map((group) => group.label)).toEqual([
      'Local Forge',
      'valhalla',
    ]);
    expect(groupRavnsBy(fleet, 'engine').map((group) => group.label)).toEqual(['hermes', 'ravn']);
    expect(
      groupRavnsBy([makeRavn({ engine: undefined })], 'engine').map((group) => group.label),
    ).toEqual(['persona runtime']);
  });

  it('names flocks after their coordinator and keeps standalone ravens last', () => {
    const flockId = '33333333-3333-4333-8333-333333333333';
    const otherFlock = '44444444-4444-4444-8444-444444444444';
    const groups = groupRavnsBy(
      [
        makeRavn({ id: 'a', residentName: 'Loner' }),
        makeRavn({ id: 'b', residentName: 'Regin', flockId, flockRole: 'coordinator' }),
        makeRavn({ id: 'c', residentName: 'Peer', flockId, flockRole: 'member' }),
        makeRavn({ id: 'd', residentName: 'Solo', flockId: otherFlock, flockRole: 'member' }),
      ],
      'flock',
    );
    const labels = groups.map((group) => group.label);
    expect(labels).toHaveLength(3);
    expect(labels).toEqual(expect.arrayContaining(['Flock · Regin', 'Flock 44444444']));
    expect(labels.at(-1)).toBe('Standalone');
  });
});

describe('defaultRavn and usage', () => {
  it('opens whatever needs attention first', () => {
    expect(defaultRavn([makeRavn({ id: 'a' }), failedRavn({ id: 'b' })])?.id).toBe('b');
    expect(defaultRavn([])).toBeNull();
  });

  it('sums fleet usage', () => {
    expect(
      fleetUsage([makeRavn(), makeRavn({ tokenCount: undefined, costUsd: undefined })]),
    ).toEqual({ tokens: 34_904, costUsd: 0.07, messages: 24 });
  });

  it('formats tokens and money', () => {
    expect(formatTokens(12)).toBe('12');
    expect(formatTokens(34_904)).toBe('34.9k');
    expect(formatTokens(1_842_210)).toBe('1.8M');
    expect(formatUsd(0)).toBe('$0');
    expect(formatUsd(0.004)).toBe('<$0.01');
    expect(formatUsd(9.123)).toBe('$9.12');
  });
});
