import { describe, expect, it, vi } from 'vitest';
import { ApiClientError } from '@niuulabs/query';
import { buildVolundrHttpAdapter } from './http';

function makeClient() {
  return {
    basePath: 'http://localhost:8080/api/v1/forge',
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn().mockResolvedValue(undefined),
    patch: vi.fn(),
    put: vi.fn(),
  };
}

const hostPayload = {
  id: 'h-1',
  name: 'glad',
  tags: ['site:hnoss'],
  state: 'ready',
  health: 'healthy',
  service: 'active',
  remove_when_empty: false,
  execution: 'host@2026-11-07',
  runtime: 'host',
  connector: 'direct-ssh',
  connection: {
    kind: 'direct_ssh',
    target: '192.168.90.35:22',
    connected: true,
    since: '2026-11-06T11:57:00Z',
    last_error: null,
  },
  slots: null,
  sessions: [{ slot: 0, session_id: 's-1', state: 'busy' }],
  slots_free: 0,
  facts: { cpus: 48, memory_mib: 196_608, disk_free_mib: 1_600_000, kernel: '6.8.0', gpus: 0 },
  suggested_slots: { slots: 11, lines: ['CPU (48 − 4) ÷ 4 = 11'] },
  findings: [],
  host_key_fingerprint: 'SHA256:abc',
  registered_at: '2026-11-06T11:57:00Z',
  registered_by: 'jozef',
  last_checked_at: null,
};

const listPayload = {
  pool_id: 'valhalla-hosts',
  hosts: [hostPayload],
  summary: {
    hosts: 1,
    by_state: { ready: 1 },
    slots_total: 0,
    slots_free: 0,
    free_by_tag_set: [{ tags: ['site:hnoss'], free: 0 }],
    warm_targets: [{ tags: [], match: 'all', min: 4, free: 0, met: false }],
  },
  connectors: [{ binding_id: 'direct-ssh', kind: 'direct_ssh', label: 'Forge connects over SSH' }],
  runtimes: [
    {
      execution: 'host@2026-11-07',
      runtime: 'host',
      label: 'Host',
      exclusive_only: false,
      allowed: true,
      refusal: null,
    },
  ],
  pairing_codes: [
    {
      id: 'c-1',
      execution: 'host@2026-11-07',
      connector: 'direct-ssh',
      tags: [],
      slots: null,
      expires_at: '2026-11-06T12:10:00Z',
      created_by: 'jozef',
      consumed_by_host_id: null,
    },
  ],
};

describe('compute hosts over HTTP', () => {
  it('lists the hosts of one Forge through the Guild facade', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(listPayload);
    const overview = await buildVolundrHttpAdapter(client).getComputeHosts('forge/a');
    expect(client.get).toHaveBeenCalledWith('/compute/hosts?instance_id=forge%2Fa');
    expect(overview?.poolId).toBe('valhalla-hosts');
    expect(overview?.summary).toEqual({
      hosts: 1,
      byState: { ready: 1 },
      slotsTotal: 0,
      slotsFree: 0,
      freeByTagSet: [{ tags: ['site:hnoss'], free: 0 }],
      warmTargets: [{ tags: [], match: 'all', min: 4, free: 0, met: false }],
    });
    expect(overview?.connectors).toEqual([
      { bindingId: 'direct-ssh', kind: 'direct_ssh', label: 'Forge connects over SSH' },
    ]);
    expect(overview?.runtimes[0]).toMatchObject({ exclusiveOnly: false, allowed: true });
    expect(overview?.pairingCodes[0]).toMatchObject({
      id: 'c-1',
      expiresAt: '2026-11-06T12:10:00Z',
      consumedByHostId: null,
    });
    expect(overview?.hosts[0]).toMatchObject({
      id: 'h-1',
      removeWhenEmpty: false,
      connection: { kind: 'direct_ssh', connected: true, lastError: null },
      sessions: [{ slot: 0, sessionId: 's-1', state: 'busy' }],
      slotsFree: 0,
      suggestedSlots: { slots: 11 },
      hostKeyFingerprint: 'SHA256:abc',
      registeredBy: 'jozef',
      lastCheckedAt: null,
    });
    expect(overview?.hosts[0]?.facts).toEqual({
      cpus: 48,
      memoryMib: 196_608,
      diskFreeMib: 1_600_000,
      kernel: '6.8.0',
      arch: null,
      cgroupV2: null,
      gpus: 0,
      os: null,
    });
  });

  it('reads a host with no facts yet as unmeasured', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      ...listPayload,
      hosts: [{ ...hostPayload, facts: null }],
    });
    const overview = await buildVolundrHttpAdapter(client).getComputeHosts('forge');
    expect(overview?.hosts[0]?.facts.cpus).toBeNull();
  });

  it('answers null for a Forge without compute hosts (404)', async () => {
    const client = makeClient();
    client.get.mockRejectedValue(new ApiClientError('API request failed: 404', 404, 'Not Found'));
    expect(await buildVolundrHttpAdapter(client).getComputeHosts('forge')).toBeNull();
  });

  it('rejects every other failure', async () => {
    const client = makeClient();
    client.get.mockRejectedValue(new ApiClientError('API request failed: 403', 403, 'Forbidden'));
    await expect(buildVolundrHttpAdapter(client).getComputeHosts('forge')).rejects.toMatchObject({
      status: 403,
    });
  });

  it('refuses an answer that is not a host list', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({ pool_id: 'p' });
    await expect(buildVolundrHttpAdapter(client).getComputeHosts('forge')).rejects.toThrow(
      'Invalid compute hosts from forge',
    );
    client.get.mockResolvedValue(undefined);
    await expect(buildVolundrHttpAdapter(client).getComputeHosts('forge')).rejects.toThrow(
      'Invalid compute hosts from forge',
    );
  });

  it('reads the tag sets hosts can take sessions on', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({ tag_sets: [['gpu', 'site:hnoss'], ['site:hnoss']] });
    expect(await buildVolundrHttpAdapter(client).getComputeHostTagSets('forge')).toEqual([
      ['gpu', 'site:hnoss'],
      ['site:hnoss'],
    ]);
    expect(client.get).toHaveBeenCalledWith('/compute/hosts/tag-sets?instance_id=forge');
  });

  it('answers null tag sets for a Forge without compute hosts, and refuses a bad answer', async () => {
    const client = makeClient();
    client.get.mockRejectedValueOnce(new ApiClientError('API request failed: 404', 404));
    expect(await buildVolundrHttpAdapter(client).getComputeHostTagSets('forge')).toBeNull();
    client.get.mockRejectedValueOnce(new ApiClientError('API request failed: 500', 500));
    await expect(buildVolundrHttpAdapter(client).getComputeHostTagSets('forge')).rejects.toThrow(
      'API request failed: 500',
    );
    client.get.mockResolvedValueOnce({});
    await expect(buildVolundrHttpAdapter(client).getComputeHostTagSets('forge')).rejects.toThrow(
      'Invalid host tag sets from forge',
    );
  });

  it('mints a pairing code and returns the commands', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({
      ...listPayload.pairing_codes[0],
      tags: ['site:hnoss'],
      code: 'jwt',
      command: 'curl … | sudo sh',
      command_wget: 'wget … | sudo sh',
      script: {
        filename: 'niuu-join.sh',
        sha256: 'ab12',
        content: '#!/bin/sh\n',
        commands: ['curl -fsSL -o niuu-join.sh …', 'sudo sh niuu-join.sh'],
      },
    });
    const created = await buildVolundrHttpAdapter(client).createPairingCode('forge', {
      execution: 'host@2026-11-07',
      connector: 'direct-ssh',
      tags: ['site:hnoss'],
      slots: null,
    });
    expect(client.post).toHaveBeenCalledWith('/compute/hosts/pairing-codes?instance_id=forge', {
      execution: 'host@2026-11-07',
      connector: 'direct-ssh',
      tags: ['site:hnoss'],
      slots: null,
    });
    expect(created).toMatchObject({
      id: 'c-1',
      code: 'jwt',
      command: 'curl … | sudo sh',
      commandWget: 'wget … | sudo sh',
      expiresAt: '2026-11-06T12:10:00Z',
      script: {
        filename: 'niuu-join.sh',
        sha256: 'ab12',
        content: '#!/bin/sh\n',
        commands: ['curl -fsSL -o niuu-join.sh …', 'sudo sh niuu-join.sh'],
      },
    });
  });

  it('refuses a minted code without the script to read first', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({
      ...listPayload.pairing_codes[0],
      code: 'jwt',
      command: 'curl … | sudo sh',
      command_wget: 'wget … | sudo sh',
    });
    await expect(
      buildVolundrHttpAdapter(client).createPairingCode('forge', {
        execution: 'x',
        connector: 'y',
        tags: [],
        slots: null,
      }),
    ).rejects.toThrow('Invalid pairing code from forge');
  });

  it('lets a refused pairing code reject with the platform reason', async () => {
    const client = makeClient();
    client.post.mockRejectedValue(new ApiClientError('API request failed: 422', 422, 'bad tag'));
    await expect(
      buildVolundrHttpAdapter(client).createPairingCode('forge', {
        execution: 'x',
        connector: 'y',
        tags: [],
        slots: 0,
      }),
    ).rejects.toMatchObject({ status: 422, detail: 'bad tag' });
  });

  it('revokes a pairing code', async () => {
    const client = makeClient();
    await buildVolundrHttpAdapter(client).revokePairingCode('forge', 'c/1');
    expect(client.delete).toHaveBeenCalledWith(
      '/compute/hosts/pairing-codes/c%2F1?instance_id=forge',
    );
  });

  it('changes tags, slots or name with PATCH', async () => {
    const client = makeClient();
    client.patch.mockResolvedValue({ ...hostPayload, tags: ['gpu'] });
    const updated = await buildVolundrHttpAdapter(client).updateComputeHost('forge', 'h-1', {
      tags: ['gpu'],
    });
    expect(client.patch).toHaveBeenCalledWith('/compute/hosts/h-1?instance_id=forge', {
      tags: ['gpu'],
    });
    expect(updated.tags).toEqual(['gpu']);
  });

  it('lets a refused slot change reject with its arithmetic', async () => {
    const client = makeClient();
    client.patch.mockRejectedValue(
      new ApiClientError('API request failed: 422', 422, 'CPU (48 − 4) ÷ 4 = 11'),
    );
    await expect(
      buildVolundrHttpAdapter(client).updateComputeHost('forge', 'h-1', { slots: 40 }),
    ).rejects.toMatchObject({ detail: 'CPU (48 − 4) ÷ 4 = 11' });
  });

  it('drains with and without removal', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({ ...hostPayload, state: 'draining' });
    const service = buildVolundrHttpAdapter(client);
    expect((await service.drainComputeHost('forge', 'h-1', true)).state).toBe('draining');
    expect(client.post).toHaveBeenCalledWith('/compute/hosts/h-1/drain?instance_id=forge', {
      remove: true,
    });
    await service.drainComputeHost('forge', 'h-1', false);
    expect(client.post).toHaveBeenLastCalledWith('/compute/hosts/h-1/drain?instance_id=forge', {
      remove: false,
    });
  });

  it('resumes and checks a host', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(hostPayload);
    const service = buildVolundrHttpAdapter(client);
    await service.resumeComputeHost('forge', 'h-1');
    expect(client.post).toHaveBeenLastCalledWith('/compute/hosts/h-1/resume?instance_id=forge');
    await service.checkComputeHost('forge', 'h-1');
    expect(client.post).toHaveBeenLastCalledWith('/compute/hosts/h-1/check?instance_id=forge');
    await service.reprepareComputeHost('forge', 'h-1');
    expect(client.post).toHaveBeenLastCalledWith('/compute/hosts/h-1/reprepare?instance_id=forge');
  });

  it('refuses an action answer that is not a host', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({});
    await expect(buildVolundrHttpAdapter(client).checkComputeHost('forge', 'h-1')).rejects.toThrow(
      'Invalid compute host from forge',
    );
    client.post.mockResolvedValue(undefined);
    await expect(buildVolundrHttpAdapter(client).resumeComputeHost('forge', 'h-1')).rejects.toThrow(
      'Invalid compute host from forge',
    );
  });

  it('removes a host and reports the command to clean it when Forge could not', async () => {
    const client = makeClient();
    client.delete.mockResolvedValueOnce({
      removed: true,
      host_cleanup_command: 'sudo niuu-host-leave',
    });
    const service = buildVolundrHttpAdapter(client);
    expect(await service.removeComputeHost('forge', 'h-1')).toEqual({
      removed: true,
      hostCleanupCommand: 'sudo niuu-host-leave',
    });
    expect(client.delete).toHaveBeenCalledWith('/compute/hosts/h-1?instance_id=forge');
    client.delete.mockResolvedValueOnce({ removed: true, host_cleanup_command: null });
    expect((await service.removeComputeHost('forge', 'h-1')).hostCleanupCommand).toBeNull();
    client.delete.mockResolvedValueOnce({ removed: true });
    expect((await service.removeComputeHost('forge', 'h-1')).hostCleanupCommand).toBeNull();
  });

  it('refuses a removal answer without the result, and rejects a host that still runs sessions', async () => {
    const client = makeClient();
    const service = buildVolundrHttpAdapter(client);
    client.delete.mockResolvedValueOnce({});
    await expect(service.removeComputeHost('forge', 'h-1')).rejects.toThrow(
      'Invalid host removal from forge',
    );
    client.delete.mockResolvedValueOnce(undefined);
    await expect(service.removeComputeHost('forge', 'h-1')).rejects.toThrow(
      'Invalid host removal from forge',
    );
    client.delete.mockRejectedValueOnce(
      new ApiClientError('API request failed: 409', 409, 'sessions still run'),
    );
    await expect(service.removeComputeHost('forge', 'h-1')).rejects.toMatchObject({ status: 409 });
  });
});
