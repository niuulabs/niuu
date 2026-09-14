import { describe, expect, it } from 'vitest';
import type { Ravn } from './ravn';
import {
  latestActionFor,
  orderResidents,
  pendingReviewsFor,
  personaUsage,
  realmForRavn,
  residentActivity,
  valkyrieForRavn,
  type ValkyrieActionView,
  type ValkyrieResidentView,
} from './residentBoard';

function makeRavn(overrides: Partial<Ravn> = {}): Ravn {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    personaName: 'realm-lexi-api',
    status: 'active',
    model: 'gpt-5.6',
    createdAt: '2026-07-13T10:00:00Z',
    kind: 'resident',
    managed: true,
    ...overrides,
  } as Ravn;
}

const muninn: ValkyrieResidentView = {
  id: 'v-1',
  name: 'muninn',
  environmentId: 'env-k8s-lexi-api',
  wakefulness: 'wakeful',
};

describe('valkyrieForRavn', () => {
  it('matches on the environment slug behind the deployment prefix', () => {
    expect(valkyrieForRavn([muninn], makeRavn())).toBe(muninn);
    expect(valkyrieForRavn([muninn], makeRavn({ residentName: 'lexi-api' }))).toBe(muninn);
  });

  it('matches on the resident name when environments are named differently', () => {
    const named: ValkyrieResidentView = { ...muninn, environmentId: 'env-prod', name: 'lexi-api' };
    expect(valkyrieForRavn([named], makeRavn())).toBe(named);
  });

  it('returns null when the two fleets do not line up', () => {
    expect(valkyrieForRavn([muninn], makeRavn({ personaName: 'realm-other' }))).toBeNull();
    expect(valkyrieForRavn([], makeRavn())).toBeNull();
  });
});

describe('realmForRavn', () => {
  const realms = [{ slug: 'lexi-api', name: 'Lexi API' }];

  it('only links a realm the governance service actually lists', () => {
    expect(realmForRavn(realms, makeRavn())?.name).toBe('Lexi API');
    expect(realmForRavn(realms, makeRavn({ personaName: 'realm-ghost' }))).toBeNull();
    expect(realmForRavn([], makeRavn())).toBeNull();
    expect(realmForRavn(realms, makeRavn({ personaName: '' }))).toBeNull();
  });
});

describe('pendingReviewsFor', () => {
  const reviews = [
    { itemId: 'r1', environmentId: 'env-k8s-lexi-api' },
    { itemId: 'r2', environmentId: 'env-k8s-other' },
  ];

  it('keeps only what waits on this resident’s environment', () => {
    expect(pendingReviewsFor(reviews, muninn).map((item) => item.itemId)).toEqual(['r1']);
    expect(pendingReviewsFor(reviews, null)).toEqual([]);
  });
});

describe('latestActionFor', () => {
  const actions: ValkyrieActionView[] = [
    {
      id: 'a1',
      title: 'Rotated the staging certificate',
      ownerValkyrieId: 'v-1',
      environmentId: 'env-k8s-lexi-api',
      startedAt: '2026-09-14T08:00:00Z',
      finishedAt: '2026-09-14T08:05:00Z',
    },
    {
      id: 'a2',
      title: 'Opened a repair session',
      ownerValkyrieId: 'v-1',
      environmentId: 'env-k8s-lexi-api',
      startedAt: '2026-09-14T09:00:00Z',
    },
    {
      id: 'a3',
      title: 'Someone else’s work',
      ownerValkyrieId: 'v-2',
      environmentId: 'env-k8s-other',
      finishedAt: '2026-09-14T10:00:00Z',
    },
  ];

  it('picks this resident’s newest action', () => {
    expect(latestActionFor(actions, muninn)?.id).toBe('a2');
    expect(latestActionFor(actions, null)).toBeNull();
    expect(latestActionFor([], muninn)).toBeNull();
  });
});

describe('residentActivity', () => {
  it('prefers the action title, then a last-acted stamp, then last seen', () => {
    const action: ValkyrieActionView = {
      id: 'a1',
      title: 'Rotated the certificate',
      ownerValkyrieId: 'v-1',
      environmentId: 'env-k8s-lexi-api',
      finishedAt: '2026-09-14T08:05:00Z',
    };
    expect(residentActivity(makeRavn(), muninn, action)).toEqual({
      label: 'Rotated the certificate',
      at: '2026-09-14T08:05:00Z',
    });
    expect(
      residentActivity(makeRavn(), { ...muninn, lastActionAt: '2026-09-14T07:00:00Z' }, null),
    ).toEqual({ label: 'last acted', at: '2026-09-14T07:00:00Z' });
    expect(residentActivity(makeRavn({ updatedAt: '2026-09-13T07:00:00Z' }), null, null)).toEqual({
      label: 'last seen',
      at: '2026-09-13T07:00:00Z',
    });
  });

  it('says nothing rather than inventing a line', () => {
    expect(residentActivity(makeRavn(), null, null)).toBeNull();
  });
});

describe('orderResidents', () => {
  it('puts what needs an answer first, then the awake ones, then names', () => {
    const waiting = makeRavn({ id: 'w', residentName: 'zeta', status: 'idle' });
    const active = makeRavn({ id: 'a', residentName: 'beta', status: 'active' });
    const alsoActive = makeRavn({ id: 'b', residentName: 'alpha', status: 'active' });
    const idle = makeRavn({ id: 'i', residentName: 'gamma', status: 'idle' });

    const ordered = orderResidents([idle, active, waiting, alsoActive], (ravn) =>
      ravn.id === 'w' ? 2 : 0,
    );
    expect(ordered.map((ravn) => ravn.residentName)).toEqual(['zeta', 'alpha', 'beta', 'gamma']);
  });
});

describe('personaUsage', () => {
  it('counts residents running a persona', () => {
    const ravens = [
      makeRavn(),
      makeRavn({ id: 'x', personaName: 'realm-lexi-api' }),
      makeRavn({ id: 'y', personaName: 'other' }),
    ];
    expect(personaUsage(ravens, 'realm-lexi-api')).toBe(2);
    expect(personaUsage(ravens, 'nobody')).toBe(0);
  });
});
