import { describe, expect, it } from 'vitest';
import type { Ravn } from '@niuulabs/plugin-ravn';
import type {
  RealmSummary,
  RealmTrustGrant,
  ReviewItem,
  ValkyrieDashboard,
  ValkyrieResident,
} from '@niuulabs/plugin-valkyrie';
import type { VolundrSession } from '@niuulabs/plugin-volundr';
import {
  activeSessionCount,
  bindingFromGrants,
  buildRealmView,
  latestGrants,
  ravnForRealm,
  residentForRealm,
  reviewsForRealm,
  sessionsForRealm,
} from './join';

const realm: RealmSummary = {
  id: 'r1',
  slug: 'lexi-api',
  name: 'Lexi API',
  sleipnir_domain: 'code',
  owner_id: null,
  instance_id: 'inst-1',
  autonomy_profile: 'balanced',
  created_at: '2026-09-13T00:00:00Z',
  updated_at: '2026-09-13T00:00:00Z',
};

const resident = {
  id: 'v1',
  name: 'lexi-api',
  environmentId: 'env-k8s-lexi-api',
  persona: 'realm-lexi-api',
  specialty: 'code',
  wakefulness: 'wakeful',
  autonomyMode: 'guarded',
  status: 'online',
  confidence: 0.7,
  inboxSubjects: [],
  toolCount: 3,
} as unknown as ValkyrieResident;

const dashboard = { valkyries: [resident], environments: [] } as unknown as ValkyrieDashboard;

const ravn = {
  id: 'ravn-1',
  residentName: 'lexi-api',
  personaName: '',
  status: 'active',
} as unknown as Ravn;

const grants: RealmTrustGrant[] = [
  {
    id: 'g1',
    realm_id: 'r1',
    action_class: 'observe',
    target: 'niuulabs/lexi-api',
    level: 2,
    limits: {
      template: 'product-resident',
      tracker_board: 'board-1',
      branch: 'dev',
      mount_target: 'ymir',
    },
    granted_by: null,
    granted_at: '2026-09-13T00:00:00Z',
  },
  {
    id: 'g2',
    realm_id: 'r1',
    action_class: 'deploy',
    target: '*',
    level: 1,
    limits: {},
    granted_by: null,
    granted_at: '2026-09-13T00:00:00Z',
  },
  {
    id: 'g3',
    realm_id: 'r1',
    action_class: 'deploy',
    target: '*',
    level: 2,
    limits: {},
    granted_by: null,
    granted_at: '2026-09-13T00:01:00Z',
  },
];

const reviews = [
  { itemId: 'rv1', environmentId: 'env-k8s-lexi-api', status: 'pending' },
  { itemId: 'rv2', environmentId: 'env-k8s-other', status: 'pending' },
  { itemId: 'rv3', environmentId: 'env-k8s-lexi-api', status: 'approved' },
] as unknown as ReviewItem[];

const sessions = [
  { id: 's1', personaName: 'realm-lexi-api', status: 'running' },
  { id: 's2', personaName: 'realm-lexi-api', status: 'completed' },
  { id: 's3', personaName: 'someone-else', status: 'running' },
] as unknown as VolundrSession[];

describe('realm joins', () => {
  it('finds the resident by environment id, then by name', () => {
    expect(residentForRealm(dashboard, 'lexi-api')).toBe(resident);
    expect(residentForRealm(dashboard, 'other')).toBeNull();
    expect(residentForRealm(undefined, 'lexi-api')).toBeNull();
  });

  it('finds the ravn by resident name, then by persona', () => {
    expect(ravnForRealm([ravn], 'lexi-api')).toBe(ravn);
    const byPersona = {
      ...ravn,
      residentName: undefined,
      personaName: 'realm-lexi-api',
    } as unknown as Ravn;
    expect(ravnForRealm([byPersona], 'lexi-api')).toBe(byPersona);
    expect(ravnForRealm(undefined, 'lexi-api')).toBeNull();
  });

  it('prefers the explicit realmId link over the naming convention', () => {
    const linked = { ...ravn, id: 'ravn-2', residentName: 'not-lexi-api', realmId: 'r1' };
    const byName = { ...ravn, id: 'ravn-3', residentName: 'lexi-api' };
    expect(ravnForRealm([byName, linked], 'lexi-api', 'r1')).toBe(linked);
  });

  it('falls back to the naming convention only for a ravn with no realmId at all', () => {
    const unlinked = { ...ravn, id: 'ravn-4', residentName: 'lexi-api', realmId: undefined };
    expect(ravnForRealm([unlinked], 'lexi-api', 'r1')).toBe(unlinked);
  });

  it('never matches a ravn linked to a different realm, even by name/persona', () => {
    const otherRealmsRavn = { ...ravn, id: 'ravn-5', residentName: 'lexi-api', realmId: 'r-other' };
    expect(ravnForRealm([otherRealmsRavn], 'lexi-api', 'r1')).toBeNull();
  });

  it('keeps only the sessions the realm persona started', () => {
    expect(sessionsForRealm(sessions, 'lexi-api').map((s) => s.id)).toEqual(['s1', 's2']);
    expect(activeSessionCount(sessionsForRealm(sessions, 'lexi-api'))).toBe(1);
    expect(sessionsForRealm(undefined, 'lexi-api')).toEqual([]);
  });

  it('keeps only the reviews addressed to the realm environment', () => {
    expect(reviewsForRealm(reviews, resident).map((r) => r.itemId)).toEqual(['rv1', 'rv3']);
    expect(reviewsForRealm(reviews, null)).toEqual([]);
  });

  it('reads the binding back from the observe grant', () => {
    expect(bindingFromGrants(grants)).toEqual({
      template: 'product-resident',
      repo: 'niuulabs/lexi-api',
      branch: 'dev',
      trackerBoard: 'board-1',
      bugBoard: undefined,
      mountTarget: 'ymir',
    });
    expect(bindingFromGrants([])).toBeNull();
  });

  it('keeps the latest grant per action class', () => {
    expect(latestGrants(grants)).toEqual([
      { actionClass: 'observe', level: 2, target: 'niuulabs/lexi-api' },
      { actionClass: 'deploy', level: 2, target: '*' },
    ]);
  });

  it('builds the realm view from every part', () => {
    const view = buildRealmView({ realm, dashboard, ravens: [ravn], grants, reviews, sessions });
    expect(view).toMatchObject({
      slug: 'lexi-api',
      personaName: 'realm-lexi-api',
      residentName: 'lexi-api',
      environmentId: 'env-k8s-lexi-api',
      wakefulness: 'wakeful',
      ravnId: 'ravn-1',
      pendingReviews: 1,
      runningSessions: 1,
    });
    expect(view.binding?.trackerBoard).toBe('board-1');
  });
});
