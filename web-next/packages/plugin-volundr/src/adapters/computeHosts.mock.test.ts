import { describe, expect, it } from 'vitest';
import { createMockComputeHosts, seedComputeHosts } from './computeHosts.mock';
import { createMockVolundrService } from './mock';

const NOW = Date.parse('2026-11-06T12:00:00Z');

function service(options: Parameters<typeof createMockComputeHosts>[0] = {}) {
  return createMockComputeHosts({ now: () => NOW, ...options });
}

describe('mock compute hosts', () => {
  it('is part of the mock Forge service', async () => {
    const overview = await createMockVolundrService().getComputeHosts('forge');
    expect(overview?.hosts).toHaveLength(8);
  });

  it('summarises hosts by state, free slots and tag set', async () => {
    const overview = await service().getComputeHosts('forge');
    expect(overview?.summary.hosts).toBe(8);
    expect(overview?.summary.byState).toMatchObject({ in_service: 3, draining: 1, pending: 1 });
    expect(overview?.summary.slotsFree).toBe(8);
    expect(overview?.summary.warmTargets.map((target) => target.met)).toEqual([true, false]);
    expect(overview?.runtimes.find((runtime) => !runtime.allowed)?.refusal).toMatch(/root Docker/);
  });

  it('answers null for a Forge without hosts', async () => {
    const none = service({ unavailable: true });
    expect(await none.getComputeHosts('forge')).toBeNull();
    expect(await none.getComputeHostTagSets('forge')).toBeNull();
  });

  it('lists the tag sets of hosts that can take sessions', async () => {
    expect(await service().getComputeHostTagSets('forge')).toEqual([
      ['fast-disk', 'site:hnoss'],
      ['site:hnoss'],
      ['gpu', 'site:hnoss'],
    ]);
  });

  it('mints a code that is listed until it is revoked or expires', async () => {
    const hosts = service();
    const code = await hosts.createPairingCode('forge', {
      execution: 'host@2026-11-07',
      connector: 'direct-ssh',
      tags: ['gpu'],
      slots: null,
    });
    expect(code.command).toContain(code.code);
    expect(code.commandWget).toContain('wget');
    expect(code.script.content).toContain(code.code);
    expect(code.script.sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(code.script.commands).toEqual([
      expect.stringContaining(`-o niuu-join.sh -H 'Authorization: Bearer ${code.code}'`),
      `echo '${code.script.sha256}  niuu-join.sh' | sha256sum -c -`,
      'less niuu-join.sh',
      'sudo sh niuu-join.sh',
    ]);
    expect((await hosts.getComputeHosts('forge'))?.pairingCodes.map((item) => item.id)).toEqual([
      code.id,
    ]);
    await hosts.revokePairingCode('forge', code.id);
    expect((await hosts.getComputeHosts('forge'))?.pairingCodes).toEqual([]);
    await expect(hosts.revokePairingCode('forge', code.id)).rejects.toMatchObject({ status: 404 });
  });

  it('changes tags and sets the slot count of a joining host', async () => {
    const hosts = service();
    const glad = seedComputeHosts()[6]!;
    const tagged = await hosts.updateComputeHost('forge', glad.id, { tags: ['x'], name: 'glad2' });
    expect(tagged).toMatchObject({ tags: ['x'], name: 'glad2' });
    expect(await hosts.updateComputeHost('forge', glad.id, { slots: 11 })).toMatchObject({
      slots: 11,
      slotsFree: 11,
    });
  });

  it('refuses a slot change on a host that is in service', async () => {
    const hrimfaxi = seedComputeHosts()[0]!;
    await expect(
      service().updateComputeHost('forge', hrimfaxi.id, { slots: 4 }),
    ).rejects.toMatchObject({ status: 409 });
    expect(
      await service().updateComputeHost('forge', hrimfaxi.id, { slots: hrimfaxi.slots! }),
    ).toMatchObject({ slots: 15 });
  });

  it('drains a host, then resumes it once out of service', async () => {
    const hosts = service();
    const skinfaxi = seedComputeHosts()[1]!;
    expect(await hosts.drainComputeHost('forge', skinfaxi.id, true)).toMatchObject({
      state: 'draining',
      removeWhenEmpty: true,
    });
    await expect(hosts.resumeComputeHost('forge', skinfaxi.id)).rejects.toMatchObject({
      status: 409,
    });
  });

  it('cancels a drain that does not remove the host', async () => {
    const hosts = service();
    const skinfaxi = seedComputeHosts()[1]!;
    await hosts.drainComputeHost('forge', skinfaxi.id, false);
    expect(await hosts.resumeComputeHost('forge', skinfaxi.id)).toMatchObject({
      state: 'in_service',
      service: 'active',
    });
    const glad = seedComputeHosts()[6]!;
    const empty = service({ hosts: [{ ...glad, state: 'draining', service: 'draining' }] });
    expect(await empty.resumeComputeHost('forge', glad.id)).toMatchObject({ state: 'ready' });
  });

  it('re-prepares only an empty host', async () => {
    const hosts = service();
    const [hrimfaxi, , , , , , , hofvarpnir] = seedComputeHosts();
    await expect(hosts.reprepareComputeHost('forge', hrimfaxi!.id)).rejects.toMatchObject({
      status: 409,
    });
    expect(await hosts.reprepareComputeHost('forge', hofvarpnir!.id)).toMatchObject({
      state: 'out_of_service',
      lastCheckedAt: '2026-11-06T12:00:00.000Z',
    });
    const idle = { ...hrimfaxi!, sessions: [] };
    expect(await service({ hosts: [idle] }).reprepareComputeHost('forge', idle.id)).toMatchObject({
      state: 'ready',
    });
  });

  it('resumes an out-of-service host', async () => {
    const out = { ...seedComputeHosts()[1]!, state: 'out_of_service' as const };
    const hosts = service({ hosts: [out] });
    expect(await hosts.resumeComputeHost('forge', out.id)).toMatchObject({ state: 'ready' });
  });

  it('checks a host now', async () => {
    const glad = seedComputeHosts()[6]!;
    const checked = await service().checkComputeHost('forge', glad.id);
    expect(checked.lastCheckedAt).toBe('2026-11-06T12:00:00.000Z');
  });

  it('removes only an empty host and says nothing to clean for a reached one', async () => {
    const [, skinfaxi, , , , , glad] = seedComputeHosts();
    const hosts = service({ hosts: [skinfaxi!, glad!] });
    await expect(hosts.removeComputeHost('forge', skinfaxi!.id)).rejects.toMatchObject({
      status: 409,
    });
    expect(await hosts.removeComputeHost('forge', glad!.id)).toEqual({
      removed: true,
      hostCleanupCommand: null,
    });
    expect((await hosts.getComputeHosts('forge'))?.hosts).toHaveLength(1);
  });

  it('names the cleanup command when the host cannot be reached', async () => {
    const glad = seedComputeHosts()[6]!;
    const lost = { ...glad, connection: { ...glad.connection, connected: false } };
    expect(await service({ hosts: [lost] }).removeComputeHost('forge', lost.id)).toEqual({
      removed: true,
      hostCleanupCommand: 'sudo niuu-host-leave',
    });
  });

  it('reports an unknown host as missing', async () => {
    await expect(service().checkComputeHost('forge', 'nope')).rejects.toMatchObject({
      status: 404,
    });
  });
});
