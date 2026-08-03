import { describe, it, expect } from 'vitest';
import { hostRole } from './hostRole';
import type { TopologyNode } from './index';

function host(over: Record<string, unknown> = {}): TopologyNode {
  return {
    id: 'h',
    typeId: 'host',
    label: 'h',
    parentId: null,
    status: 'healthy',
    ...over,
  } as TopologyNode;
}

describe('hostRole', () => {
  it('reads the control plane off its own roles', () => {
    expect(hostRole(host({ roles: ['control-plane', 'etcd', 'master'] }))).toBe('control-plane');
  });

  it('calls a machine with a GPU a GPU node whatever else it is', () => {
    // Every GPU node in the estate is also a worker; reporting `worker` would
    // bury the fact an operator is actually looking for.
    expect(hostRole(host({ roles: ['worker'], gpu: 'NVIDIA-L4', gpuCount: 1 }))).toBe('gpu');
  });

  it('finds the GPU on a machine that declares no role at all', () => {
    // valaskjalf's Sparks: real GPUs, no node-role label.
    expect(hostRole(host({ roles: [], gpu: 'NVIDIA-GB10', gpuCount: 1 }))).toBe('gpu');
  });

  it('calls a plain worker a worker', () => {
    expect(hostRole(host({ roles: ['worker'] }))).toBe('worker');
  });

  it('says unknown when nothing declares what the machine is for', () => {
    // eitri's Raspberry Pis carry no role label. That is the truth about them,
    // not a gap to fill in with the nearest guess.
    expect(hostRole(host({ roles: [] }))).toBe('unknown');
    expect(hostRole(host())).toBe('unknown');
  });

  it('does not read the role out of the hostname', () => {
    // A name is a convention someone can break; a label is what the scheduler
    // acts on. A machine named like a GPU worker but carrying none is not one.
    expect(hostRole(host({ label: 'valhalla-gpuworker-kfrhv-mwfr2', roles: ['worker'] }))).toBe(
      'worker',
    );
  });

  it('ignores a GPU field that says nothing', () => {
    expect(hostRole(host({ roles: ['worker'], gpu: '', gpuCount: 0 }))).toBe('worker');
  });
});
