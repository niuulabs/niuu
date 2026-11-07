import { describe, expect, it } from 'vitest';
import { createMockRealmGovernanceService, createSeedRealms, createSeedTrustGrants } from './mock';

describe('createMockRealmGovernanceService', () => {
  it('serves the seeded realms and grants', async () => {
    const service = createMockRealmGovernanceService();

    expect(await service.listRealms()).toEqual(createSeedRealms());
    expect(await service.listTrustGrants('valhalla')).toEqual(createSeedTrustGrants()['valhalla']);
  });

  it('moves a realm into a project and out again', async () => {
    const service = createMockRealmGovernanceService();

    expect((await service.setRealmProject('host-jozef', 'project-1')).project_id).toBe('project-1');
    expect((await service.getRealm('host-jozef')).project_id).toBe('project-1');
    expect((await service.setRealmProject('host-jozef', null)).project_id).toBeNull();
    await expect(service.setRealmProject('missing', null)).rejects.toThrow();
  });

  it('creates a realm in a project and refuses a duplicate slug', async () => {
    const service = createMockRealmGovernanceService();

    const realm = await service.createRealm({ slug: 'copy', name: 'Copy', project_id: 'p' });

    expect(realm.project_id).toBe('p');
    await expect(service.createRealm({ slug: 'copy', name: 'Again' })).rejects.toThrow(
      'Realm already exists: copy',
    );
  });

  it('deletes a realm and forgets its grants', async () => {
    const service = createMockRealmGovernanceService();

    await service.deleteRealm('valhalla');

    expect((await service.listRealms()).some((realm) => realm.slug === 'valhalla')).toBe(false);
    await expect(service.getRealm('valhalla')).rejects.toThrow();
    await expect(service.deleteRealm('valhalla')).rejects.toThrow();
  });

  it('creates a trust grant that later listings include', async () => {
    const service = createMockRealmGovernanceService();

    const created = await service.createTrustGrant('host-jozef', {
      action_class: 'build',
      target: '*',
      level: 4,
      limits: {},
      granted_by: 'human:operator',
    });

    expect(created).toMatchObject({
      realm_id: 'realm-host-jozef',
      action_class: 'build',
      level: 4,
      granted_by: 'human:operator',
    });

    const grants = await service.listTrustGrants('host-jozef');
    expect(grants).toHaveLength(1);
    expect(grants[0]?.id).toBe(created.id);
  });

  it('defaults granted_by to null and rejects unknown realms', async () => {
    const service = createMockRealmGovernanceService();

    const created = await service.createTrustGrant('host-jozef', {
      action_class: 'build',
      target: '*',
      level: 1,
      limits: {},
    });
    expect(created.granted_by).toBeNull();

    await expect(service.listTrustGrants('nowhere')).rejects.toThrow('Realm not found: nowhere');
    await expect(
      service.createTrustGrant('nowhere', {
        action_class: 'build',
        target: '*',
        level: 0,
        limits: {},
      }),
    ).rejects.toThrow('Realm not found: nowhere');
  });

  it('streams nothing and unsubscribes cleanly', () => {
    const service = createMockRealmGovernanceService();
    expect(service.subscribeRealmDetail('valhalla', () => {})()).toBeUndefined();
  });
});
