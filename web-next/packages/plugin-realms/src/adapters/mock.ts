import type { RealmSummary, RealmTrustGrant } from '../domain/governance';
import type { IRealmGovernanceService, RealmCreate, TrustGrantCreate } from '../ports';

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
      project_id: 'project-yggdrasil',
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
      project_id: 'project-yggdrasil',
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
        limits: {},
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

export interface RealmGovernanceSeed {
  realms?: RealmSummary[];
  grants?: Record<string, RealmTrustGrant[]>;
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

  function requireRealm(slug: string): RealmSummary {
    const realm = realms.find((entry) => entry.slug === slug);
    if (!realm) throw new Error(`Realm not found: ${slug}`);
    return realm;
  }

  return {
    subscribeRealmDetail() {
      return () => {};
    },
    async listRealms() {
      return realms.map((realm) => ({ ...realm }));
    },
    async getRealm(slug: string) {
      return { ...requireRealm(slug) };
    },
    async createRealm(request: RealmCreate) {
      if (realms.some((entry) => entry.slug === request.slug)) {
        throw new Error(`Realm already exists: ${request.slug}`);
      }
      const now = new Date().toISOString();
      const realm: RealmSummary = {
        id: `realm-${realms.length + 1}`,
        slug: request.slug,
        name: request.name,
        sleipnir_domain: request.sleipnir_domain ?? null,
        owner_id: request.owner_id ?? null,
        instance_id: request.instance_id ?? null,
        autonomy_profile: request.autonomy_profile ?? 'balanced',
        project_id: request.project_id ?? null,
        created_at: now,
        updated_at: now,
      };
      realms.push(realm);
      grants.set(realm.slug, []);
      return { ...realm };
    },
    async setRealmProject(slug: string, projectId: string | null) {
      const realm = requireRealm(slug);
      realm.project_id = projectId;
      return { ...realm };
    },
    async deleteRealm(slug: string) {
      const realm = requireRealm(slug);
      realms.splice(realms.indexOf(realm), 1);
      grants.delete(slug);
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
  };
}
