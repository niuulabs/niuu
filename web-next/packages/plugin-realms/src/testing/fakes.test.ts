import { describe, expect, it } from 'vitest';
import { createCallLog, fakeMimir, fakeRealmService, fakeTracker, fakeVolundr } from './fakes';

describe('test doubles', () => {
  it('realm service finds, creates and refuses unknown realms', async () => {
    const log = createCallLog();
    const realms = fakeRealmService(log);
    await expect(realms.getRealm('nope')).rejects.toThrow(/Realm not found/);
    const created = await realms.createRealm({ slug: 'a', name: 'A' });
    expect((await realms.getRealm('a')).id).toBe(created.id);
    expect(await realms.listTrustGrants('a')).toEqual([]);
    expect(await realms.listRealms()).toHaveLength(1);
  });

  it('mimir double honours the mountAppears and targets options', async () => {
    const log = createCallLog();
    const silent = fakeMimir(log, { mountAppears: false, targets: ['a', 'b'] });
    await silent.mounts.deployInstance!({ name: 'realm-x', backend: 'mimir', target: 'a' });
    expect(await silent.mounts.listMounts()).toEqual([]);
    expect((await silent.mounts.getDeployments!()).targets?.map((t) => t.id)).toEqual(['a', 'b']);
    await silent.pages.upsertPage('p', 'c', 'm');
    expect(log.calls).toContain('upsertPage:p@m');
  });

  it('tracker and volundr doubles answer the read calls the wizard makes', async () => {
    const log = createCallLog();
    const tracker = fakeTracker(log);
    expect((await tracker.getProject('board-1')).slug).toBe('LXA');
    expect(await tracker.listMilestones('board-1')).toEqual([]);
    expect(await tracker.listIssues('board-1')).toHaveLength(1);
    const volundr = fakeVolundr(log);
    expect(await volundr.getSessions()).toEqual([]);
    expect(await volundr.getRepos()).toHaveLength(1);
    expect(await volundr.getIntegrations()).toHaveLength(1);
    expect(await volundr.getAvailableMcpServers()).toHaveLength(1);
    expect(await volundr.testIntegration('int-1')).toEqual({ success: true });
  });
});
