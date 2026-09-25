import { describe, expect, it } from 'vitest';
import {
  createCallLog,
  fakeMimir,
  fakePersonas,
  fakeRealmService,
  fakeResidents,
  fakeTracker,
  fakeTriggers,
  fakeVolundr,
} from './fakes';

describe('test doubles', () => {
  it('realm service finds, creates and refuses unknown realms', async () => {
    const log = createCallLog();
    const realms = fakeRealmService(log);
    await expect(realms.getRealm('nope')).rejects.toThrow(/Realm not found/);
    const created = await realms.createRealm({ slug: 'a', name: 'A' });
    expect((await realms.getRealm('a')).id).toBe(created.id);
    expect(await realms.listTrustGrants('a')).toEqual([]);
    expect(await realms.listWorkflows()).toEqual([]);
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

  it('persona double stores, updates, forks and refuses unknown personas', async () => {
    const log = createCallLog();
    const personas = fakePersonas(log);
    await expect(personas.getPersona('missing')).rejects.toThrow(/Persona not found/);
    await personas.createPersona({ name: 'p', description: 'd' } as never);
    expect((await personas.getPersona('p')).description).toBe('d');
    await personas.updatePersona('p', { name: 'p', description: 'e' } as never);
    expect((await personas.getPersona('p')).description).toBe('e');
    expect((await personas.forkPersona('p', { newName: 'q' })).name).toBe('q');
    expect(await personas.listPersonas()).toEqual([]);
    expect(await personas.getPersonaYaml('p')).toBe('');
    await personas.deletePersona('p');
  });

  it('trigger double lists what it created', async () => {
    const log = createCallLog();
    const triggers = fakeTriggers(log);
    const created = await triggers.createTrigger({
      kind: 'cron',
      spec: '* * * * *',
      personaName: 'p',
      enabled: true,
    });
    expect(created.executionEnabled).toBe(true);
    expect(await triggers.listTriggers()).toHaveLength(1);
    await triggers.deleteTrigger('x');
  });

  it('fakeTriggers can report execution as disabled for this deployment', async () => {
    const log = createCallLog();
    const triggers = fakeTriggers(log, { executionEnabled: false });
    const created = await triggers.createTrigger({
      kind: 'cron',
      spec: '* * * * *',
      personaName: 'p',
      enabled: true,
    });
    expect(created.executionEnabled).toBe(false);
  });

  it('resident double deploys, lists and refuses unknown ravens', async () => {
    const log = createCallLog();
    const residents = fakeResidents(log);
    expect(await residents.listProfiles()).toHaveLength(1);
    const ravn = await residents.deploy({
      name: 'r',
      profileId: 'profile-1',
      instanceId: 'inst-1',
    });
    expect(await residents.listRavens()).toHaveLength(1);
    expect((await residents.getRaven(ravn.id)).residentName).toBe('r');
    await expect(residents.getRaven('nope')).rejects.toThrow(/Ravn not found/);
    expect(await residents.applyLifecycle(ravn, 'restart')).toBe(ravn);
    expect(await residents.getLogs(ravn)).toEqual({ entries: [], bufferTotal: 0 });
    expect(await residents.listSessions(ravn)).toEqual([]);
    await expect(residents.createSession(ravn, { title: 't' })).rejects.toThrow();
    await residents.delete(ravn);
    await residents.deleteSession(ravn, 's');
    await expect(
      fakeResidents(log, { failDeploy: true }).deploy({
        name: 'r',
        profileId: 'profile-1',
        instanceId: 'inst-1',
      }),
    ).rejects.toThrow(/not enabled/);
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
